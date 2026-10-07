"""Фоновый поток: опрашивает Riot Client, собирает статистику, пати и страны, формирует данные для UI."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from valinfo.analytics.matchstore import HistoryStore, MatchStore
from valinfo.analytics.party import detect_team_parties
from valinfo.analytics.player import fetch_player
from valinfo.analytics.post_match import parse_ultra_post_match
from valinfo.analytics.records import parse_match_summary
from valinfo.api.match import fetch_names, get_match
from valinfo.api.riot import Local, Riot, detect_region, read_lockfile
from valinfo.api.static import StaticData, load_static
from valinfo.config import COUNTRY_ON_PREGAME, FETCH_WORKERS, IDLE_REFRESH_SECONDS, POST_MATCH_RETRIES, REFRESH_SECONDS
from valinfo.country.fetcher import CountryFetcher
from valinfo.models import CountryResult, CountryStatus
from valinfo.i18n import tr
from valinfo.modes import detect_layout, mode_for
from valinfo.paths import LOCKFILE
from valinfo.settings import get_settings
from valinfo.storage.assets import AssetStore

log = logging.getLogger(__name__)

MISS_TICKS_TO_END = 2        # матч считается оконченным, только если его нет 2 тика подряд (защита от «моргания» API)


class WorkerThread(QThread):
    live_data_ready = pyqtSignal(dict)
    post_match_data_ready = pyqtSignal(dict)
    status_changed = pyqtSignal(str, dict)      # (ключ перевода, параметры) — текст собирает UI на текущем языке
    no_match = pyqtSignal()                     # сейчас никакого матча нет: UI должен очистить live-данные

    def __init__(self, assets: AssetStore):
        super().__init__()
        self._assets = assets
        self._stop_evt = threading.Event()
        self._lock = threading.RLock()
        self._live = None
        self._agents = {}
        self._roles = {}
        self._names = {}
        # Общие хранилища: каждый матч скачивается и разбирается один раз на всё приложение
        self._matches = MatchStore()
        self._history = HistoryStore()
        self._info_pool = ThreadPoolExecutor(max_workers=10)         # по одной задаче на игрока (ждут _fetch_pool)
        self._fetch_pool = ThreadPoolExecutor(max_workers=FETCH_WORKERS)   # единый пул загрузки match-details
        self._party_pool = ThreadPoolExecutor(max_workers=16)
        self._coord_pool = ThreadPoolExecutor(max_workers=4)
        self._settings = get_settings()
        self._country = None                                          # создаётся лениво, когда опция включена

    def _country_fetcher(self) -> Optional[CountryFetcher]:
        """Поиск страны — по настройке: включили на лету → создаём, выключили → новые поиски не запускаем."""
        if not self._settings.country_detection:
            return None
        if self._country is None:
            self._country = CountryFetcher(self._assets)
        return self._country

    def _say(self, key: str, **params) -> None:
        self.status_changed.emit(key, params)

    # ── жизненный цикл ──
    def stop(self) -> None:
        self._stop_evt.set()
        with self._lock:
            if self._live is not None:
                self._live["dead"] = True   # отменяет поиск стран в очереди

    def _sleep(self, sec: float) -> bool:
        """Прерываемый сон. True — пора выходить."""
        return self._stop_evt.wait(sec)

    # ── состояние live-матча ──
    @staticmethod
    def _new_live(mid, me):
        return {
            "mid": mid, "me": me, "kind": "", "map_name": "", "map_url": "", "queue": "", "layout": "teams",
            "players": [], "info": {}, "info_done": set(), "info_started": set(), "records": {},
            "groups": {},
            "party_state": {},
            "country": {},            # subject -> CountryResult
            "country_started": set(),
            "dead": False,
        }

    def _build_payload(self, st):
        players = list(st["players"])
        me = st["me"]
        order = {p["subject"]: i for i, p in enumerate(players)}
        multi = [list(g) for gs in st["groups"].values() for g in gs]
        multi.sort(key=lambda g: (0 if me in g else 1, -len(g), min(order.get(s, 0) for s in g)))
        parties = {s: i for i, g in enumerate(multi) for s in g}
        status = {}
        if self._settings.party_detection and st["layout"] != "ffa":
            for p in players:
                status[p["team"]] = "done" if st["party_state"].get(p["team"]) == "done" else "pending"
        return {
            "kind": st["kind"], "mid": st["mid"], "map_name": st["map_name"], "map_url": st["map_url"],
            "queue": st["queue"], "layout": st["layout"], "roles": self._roles, "records": dict(st["records"]),
            "players": players, "info": dict(st["info"]), "info_done": set(st["info_done"]),
            "names": dict(self._names), "agents": self._agents, "me": me,
            "parties": parties, "party_status": status,
            "countries": dict(st["country"]),
        }

    def _emit_live(self, st):
        with self._lock:
            if st["dead"] or self._live is not st:
                return
            payload = self._build_payload(st)
        self.live_data_ready.emit(payload)

    def _info_task(self, st, riot, s, seasons, season_dates, count):
        def partial(d):
            with self._lock:
                if st["dead"]:
                    return
                st["info"][s] = d
            self._emit_live(st)

        try:
            pdata, records = fetch_player(
                riot, s, seasons=seasons, season_dates=season_dates, agent_names=self._agents,
                mode=mode_for(st["queue"]), count=count, matches=self._matches, history=self._history,
                pool=self._fetch_pool, on_partial=partial)
        except Exception:
            log.debug("info task failed for %s", s, exc_info=True)
            with self._lock:
                st["info_started"].discard(s)
            return
        with self._lock:
            if st["dead"]:
                return
            st["info"][s] = pdata
            st["records"][s] = records
            st["info_done"].add(s)
        self._emit_live(st)

    def _start_countries(self, st, players, me):
        """Запускает поиск страны для каждого игрока, кроме меня и тех, у кого скрыт ник."""
        fetcher = self._country_fetcher()
        if fetcher is None:
            return
        for p in players:
            s = p["subject"]
            if s == me or (p.get("ident") or {}).get("Incognito"):
                continue
            nm = self._names.get(s)
            if not nm or not nm[0] or not nm[1]:
                continue  # ник ещё не получен — попробуем на следующем тике
            with self._lock:
                if st["dead"] or s in st["country_started"]:
                    continue
                st["country_started"].add(s)
                st["country"][s] = CountryResult(CountryStatus.LOADING)

            def done(result, s=s):
                with self._lock:
                    if st["dead"]:
                        return
                    st["country"][s] = result
                self._emit_live(st)

            fetcher.submit(nm[0], nm[1], lambda: st["dead"], done)

    def _party_task(self, st, riot, local, me, team_id, players):
        def partial(groups):
            with self._lock:
                if st["dead"]:
                    return
                st["groups"][team_id] = groups
            self._emit_live(st)

        try:
            groups = detect_team_parties(riot, local, me, players, team_id,
                                         self._matches, self._history, self._party_pool, on_partial=partial)
        except Exception:
            log.debug("party task failed", exc_info=True)
            with self._lock:
                groups = st["groups"].get(team_id, [])
        with self._lock:
            if st["dead"]:
                return
            st["groups"][team_id] = groups
            st["party_state"][team_id] = "done"
        self._emit_live(st)

    def _drop_live(self) -> None:
        """Матча нет: гасим live-состояние и сообщаем UI, чтобы он очистил таблицы (никаких «обрывков» прошлой игры)."""
        with self._lock:
            if self._live is not None:
                self._live["dead"] = True
                self._live = None
        self.no_match.emit()

    # ── пост-матч ──
    def _countries_for_post_match(self, parsed, ended_st, me) -> dict:
        """Страны, полученные в live-режиме, → по игрокам завершённого матча (puuid -> dict)."""
        live = dict(ended_st["country"]) if ended_st else {}
        hidden = {p["subject"] for p in (ended_st["players"] if ended_st else [])
                  if (p.get("ident") or {}).get("Incognito")}
        out = {}
        for pl in parsed["players"]:
            s = pl["puuid"]
            r = live.get(s)
            if r is not None and r.status is CountryStatus.LOADING:
                r = CountryResult(CountryStatus.INTERRUPTED, detail=tr("country.detail.interrupted"))
            if r is None:
                fetcher = self._country_fetcher()
                if fetcher is None:
                    r = CountryResult(CountryStatus.SKIPPED, detail=tr("country.detail.disabled"))
                elif s == me:
                    r = CountryResult(CountryStatus.SKIPPED, detail=tr("country.detail.you"))
                elif s in hidden:
                    r = CountryResult(CountryStatus.SKIPPED, detail=tr("country.detail.hidden"))
                else:
                    nm = pl.get("name") or ("", "")
                    r = (fetcher.lookup(nm[0], nm[1]) if nm[0] and nm[1] else None) \
                        or CountryResult(CountryStatus.SKIPPED, detail=tr("country.detail.not_searched"))
            out[s] = r.to_dict()
        return out

    def _finish_match(self, riot, mid, ended_st, me, maps) -> bool:
        """Забирает итоги матча. True — готово (или безнадёжно), False — Riot ещё не отдал детали."""
        det = riot.get(f"{riot.pd}/match-details/v1/matches/{mid}")
        if not det:
            return False
        parsed = parse_ultra_post_match(det, self._names, self._agents, me)
        if not parsed:
            return True
        parsed["map_name"] = maps.get(det.get("matchInfo", {}).get("mapId", "").lower(), tr("common.map"))
        parsed["countries"] = self._countries_for_post_match(parsed, ended_st, me)

        # Ничего на диск не пишем. Сырой JSON живёт только в этой функции; компактную версию кладём в общий
        # кэш в памяти — следующее лобби, где встретятся те же игроки, не будет качать этот матч заново.
        self._matches.put(parse_match_summary(det, self._roles))
        parsed["mode"] = (det.get("matchInfo") or {}).get("queueID") or parsed.get("mode", "")
        self.post_match_data_ready.emit(parsed)
        return True

    # ── основной цикл ──
    def _load_static_blocking(self) -> Optional[StaticData]:
        self._say("status.loading_api")
        while not self._stop_evt.is_set():
            try:
                sd = load_static()
                self._assets.preload(sd.agent_icons, sd.tier_icons)
                self._roles = sd.agent_roles
                self._matches.roles = sd.agent_roles
                return sd
            except Exception as e:
                log.warning("static data load failed: %s", e)
                self._say("status.static_error", error=type(e).__name__)
                if self._sleep(5):
                    break
        return None

    def run(self):
        sd = self._load_static_blocking()
        if sd is None:
            return

        self._agents = sd.agents
        names, maps, seasons, version = self._names, sd.maps, sd.seasons, sd.version
        season_dates = sd.season_dates

        riot = None
        local = None
        last_match_id = None
        was_in_match = False
        miss_ticks = 0
        pending = None            # {"mid", "st", "me", "tries"} — матч закончился, ждём детали от Riot
        me = None

        while not self._stop_evt.is_set():
            delay = REFRESH_SECONDS
            try:
                if not LOCKFILE.exists():
                    self._drop_live()
                    self._say("status.wait_launch")
                    if self._sleep(IDLE_REFRESH_SECONDS):
                        break
                    continue

                raw = read_lockfile()
                if local is None or local.raw != raw:   # сессия к локальному API живёт, пока lockfile не поменялся
                    local = Local(raw)
                tokens = local.get("/entitlements/v1/token")
                me = tokens["subject"]

                if riot is None:
                    region, shard = detect_region(local)
                    riot = Riot(region, shard, version)

                riot.set_tokens(tokens)
                found = get_match(riot, me)

                if found:
                    miss_ticks = 0
                    pending = None
                    kind, mid, players, map_url = found.kind, found.mid, found.players, found.map_url
                    last_match_id = mid
                    was_in_match = True
                    map_name = maps.get(map_url.lower(), tr("common.unknown_map"))
                    mode = mode_for(found.queue)
                    layout = detect_layout(mode, players)
                    subjects = [p["subject"] for p in players]
                    my_team = next((p["team"] for p in players if p["subject"] == me), players[0]["team"])
                    my_first = sorted(players, key=lambda p: p["team"] != my_team)

                    with self._lock:
                        st = self._live
                        if st is None or st["mid"] != mid:
                            if st is not None:
                                st["dead"] = True
                            st = self._live = self._new_live(mid, me)
                        st["kind"], st["map_name"], st["players"] = kind, map_name, players
                        st["map_url"], st["queue"], st["layout"] = map_url, found.queue, layout

                    try:
                        tmp = dict(names)
                        fetch_names(riot, subjects, tmp)
                        with self._lock:
                            names.update(tmp)
                    except Exception:
                        pass

                    if kind == "ingame" or COUNTRY_ON_PREGAME:
                        self._start_countries(st, players, me)

                    count = self._settings.analysis_matches
                    with self._lock:
                        todo = [p["subject"] for p in my_first if p["subject"] not in st["info_started"]]
                        st["info_started"].update(todo)
                    for s in todo:
                        self._info_pool.submit(self._info_task, st, riot, s, seasons, season_dates, count)

                    # пати имеют смысл только в командных режимах; опцию можно включить прямо посреди матча
                    if self._settings.party_detection and layout != "ffa":
                        teams = []
                        for p in my_first:
                            if p["team"] not in teams:
                                teams.append(p["team"])
                        for t in teams:
                            with self._lock:
                                if t in st["party_state"]:
                                    continue
                                st["party_state"][t] = "running"
                            self._coord_pool.submit(self._party_task, st, riot, local, me, t, list(players))

                    self._emit_live(st)

                    with self._lock:
                        loading = any(s not in st["info_done"] for s in subjects)
                    self._say("status.match", map=map_name.upper(), state=kind, mode=found.queue or "", loading=loading)

                else:
                    miss_ticks += 1
                    # матч закончился: забираем live-состояние (там лежат найденные страны) и останавливаем поиск
                    if was_in_match and last_match_id and miss_ticks >= MISS_TICKS_TO_END:
                        with self._lock:
                            ended = self._live
                            if ended is not None:
                                ended["dead"] = True
                                self._live = None
                        pending = {"mid": last_match_id, "st": ended, "me": me, "tries": 0}
                        was_in_match = False
                        self._history.clear()          # списки матчей устарели: в них нет только что сыгранного

                    if pending:
                        self._say("status.analyzing")
                        done = self._finish_match(riot, pending["mid"], pending["st"], pending["me"], maps)
                        pending["tries"] += 1
                        if done or pending["tries"] >= POST_MATCH_RETRIES:
                            if not done:
                                log.warning("match details for %s never became available", pending["mid"])
                                self.no_match.emit()
                            pending = None
                            self._say("status.waiting_game")
                    elif miss_ticks >= MISS_TICKS_TO_END or not was_in_match:
                        self._say("status.waiting_game")
                        self._drop_live()

            except FileNotFoundError:
                self._say("status.riot_off")
                self._drop_live()
                riot = None
                local = None
            except Exception as e:
                log.debug("worker tick failed", exc_info=True)
                self._say("status.conn_error", error=type(e).__name__)

            if self._sleep(delay):
                break
