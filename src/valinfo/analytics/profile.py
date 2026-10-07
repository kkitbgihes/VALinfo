"""Глубокий анализ игроков лобби: «насколько опасен», лучшая/худшая сторона, стиль, смурф-детектор.

Всё считается из записей `MatchRecord` (история, один разбор на матч — см. records.py) и считается ЛЕНИВО:
только когда пользователь открыл вкладку «Игроки» (или прогноз запросил смурф-оценку).

Принципы
--------
• Лучшие/худшие стороны определяются СРАВНЕНИЕМ С ЛОББИ (z-score по ~22 метрикам), а не абсолютными порогами.
• Чтобы стороны реже повторялись, назначение «жадное с лимитом повторов»: сначала каждая черта достаётся
  максимум одному игроку, и только если игрокам не хватило — разрешаем повторы.
• Все цифры — эвристики на небольшой выборке; надёжность оценки показывается отдельно (`reliability`).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from valinfo.analytics.lobby import Lobby, Player
from valinfo.analytics.skill import Cfg, build_model, logit, sigmoid, tier_points, weighted_history


# ═══════════════════════════ МЕТРИКИ ИГРОКА ═══════════════════════════
def _ratio(records, w, num: Callable, den: Callable) -> Optional[float]:
    n = sum(wi * num(r) for wi, r in zip(w, records))
    d = sum(wi * den(r) for wi, r in zip(w, records))
    return n / d if d > 0 else None


def compute_metrics(p: Player, cfg: Cfg, now_ms: Optional[float] = None) -> dict:
    """Все метрики игрока (None — данных не хватает). Свежие матчи весят больше."""
    recs = sorted(p.records, key=lambda r: r.started_ms, reverse=True)[: cfg.history_matches]
    m: Dict[str, Optional[float]] = {}
    n = len(recs)
    m["n"] = n
    if not n:
        return m
    hw = weighted_history(recs, cfg)
    w = [x[0] for x in hw]
    rs = [x[1] for x in hw]

    m["acs"] = _ratio(rs, w, lambda r: r.score, lambda r: max(r.rounds, 1))
    m["adr"] = _ratio(rs, w, lambda r: r.damage, lambda r: max(r.rounds, 1))
    m["kpr"] = _ratio(rs, w, lambda r: r.kills, lambda r: max(r.rounds, 1))
    m["dpr"] = _ratio(rs, w, lambda r: r.deaths, lambda r: max(r.rounds, 1))
    m["kd"] = _ratio(rs, w, lambda r: r.kills, lambda r: max(r.deaths, 1))
    m["hs"] = _ratio(rs, w, lambda r: r.hs, lambda r: max(r.hs + r.bs + r.ls, 1))
    m["fk"] = _ratio(rs, w, lambda r: r.fk, lambda r: max(r.rounds, 1))
    m["fd"] = _ratio(rs, w, lambda r: r.fd, lambda r: max(r.rounds, 1))
    fk_s = sum(wi * r.fk for wi, r in zip(w, rs))
    fd_s = sum(wi * r.fd for wi, r in zip(w, rs))
    m["entry"] = (fk_s + 1.0) / (fk_s + fd_s + 2.0)               # доля выигранных первых дуэлей (со сглаживанием)
    m["kast"] = _ratio(rs, w, lambda r: r.kast, lambda r: max(r.rounds, 1))
    m["surv"] = _ratio(rs, w, lambda r: max(r.rounds - r.deaths, 0), lambda r: max(r.rounds, 1))
    m["apr"] = _ratio(rs, w, lambda r: r.assists, lambda r: max(r.rounds, 1))
    m["mk"] = _ratio(rs, w, lambda r: r.mk3, lambda r: max(r.rounds, 1))
    m["trade"] = _ratio(rs, w, lambda r: r.trade_k, lambda r: max(r.rounds, 1))
    m["traded"] = _ratio(rs, w, lambda r: r.traded_d, lambda r: max(r.deaths, 1))
    m["spike"] = _ratio(rs, w, lambda r: r.plants + r.defuses, lambda r: max(r.rounds, 1))
    m["wr"] = _ratio(rs, w, lambda r: 1.0 if r.won else 0.0, lambda r: 1.0)
    m["dom"] = _ratio(rs, w, lambda r: r.score / max(r.rounds, 1), lambda r: max(r.lobby_acs, 1.0))
    m["placement"] = _ratio(rs, w, lambda r: r.placement or 5.5, lambda r: 1.0)

    # стороны: ADR + разница убийств/смертей за раунд на этой стороне
    def side(prefix):
        rounds = sum(getattr(r, f"{prefix}_rounds") for r in rs)
        if rounds < 12:
            return None
        dmg = sum(getattr(r, f"{prefix}_dmg") for r in rs) / rounds
        kdiff = sum(getattr(r, f"{prefix}_k") - getattr(r, f"{prefix}_d") for r in rs) / rounds
        return dmg + 90.0 * kdiff
    m["atk"], m["def"] = side("atk"), side("def")

    # стабильность: коэффициент вариации ACS (меньше = стабильнее)
    accs = [r.score / max(r.rounds, 1) for r in rs]
    if len(accs) >= 4:
        mu = sum(accs) / len(accs)
        sd = math.sqrt(sum((a - mu) ** 2 for a in accs) / len(accs))
        m["cv"] = sd / mu if mu > 0 else None

    # близкие матчи (овертайм / разница ≤ 2 раунда)
    close = [r for r in rs if r.rounds and abs(r.rw - r.rl) <= 2]
    if len(close) >= 3:
        m["close_wr"] = sum(1 for r in close if r.won) / len(close)

    # тренд: форма трёх последних матчей против предыдущих
    if n >= 6:
        a3 = sum(r.score / max(r.rounds, 1) for r in recs[:3]) / 3
        rest = recs[3:]
        a_rest = sum(r.score / max(r.rounds, 1) for r in rest) / len(rest)
        m["trend"] = a3 / a_rest - 1.0 if a_rest > 0 else None

    # сессия: цепочка матчей за последние 18 часов с паузами ≤ 4 часов
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    sess = []
    last_t = now_ms
    for r in recs:
        if not r.started_ms or now_ms - r.started_ms > 18 * 3600_000 or last_t - r.started_ms > 4 * 3600_000 + 3600_000:
            break
        sess.append(r)
        last_t = r.started_ms
    if len(sess) >= 2:
        rest = [r for r in recs if r not in sess]
        s_acs = sum(r.score for r in sess) / max(sum(r.rounds for r in sess), 1)
        base = (sum(r.score for r in rest) / max(sum(r.rounds for r in rest), 1)) if rest else None
        m["session_n"] = len(sess)
        m["session_wins"] = sum(1 for r in sess if r.won)
        m["session_acs"] = s_acs
        if base:
            m["session"] = s_acs / base - 1.0

    # знание агента и карты (в единицах относительной силы с учётом числа игр)
    def mastery(sel):
        sub = [r for r in recs if sel(r)]
        if len(sub) < 2:
            return None, len(sub)
        acs_s = sum(r.score for r in sub) / max(sum(r.rounds for r in sub), 1)
        acs_all = sum(r.score for r in recs) / max(sum(r.rounds for r in recs), 1)
        wr_s = sum(1 for r in sub if r.won) / len(sub)
        k = len(sub) / (len(sub) + 3.0)                       # мало игр — тянем к нулю
        return k * ((acs_s / acs_all - 1.0) + 0.5 * (wr_s - 0.5)), len(sub)
    if p.agent_uuid:
        m["agent_mastery"], m["agent_games"] = mastery(lambda r: r.agent == p.agent_uuid)
        if m["agent_games"] == 0 and n >= 5:
            m["agent_mastery"] = -0.18                         # на агенте ни одной игры — это само по себе минус
    mm = mastery(lambda r: r.map_url and r.map_url == _MAP.get())
    m["map_mastery"], m["map_games"] = mm
    return m


class _MapCtx:                         # контекст текущей карты для вложенной функции (без передачи через 5 уровней)
    def __init__(self): self.v = ""
    def get(self): return self.v
_MAP = _MapCtx()


def reliability(n: int) -> str:
    return "high" if n >= 8 else ("medium" if n >= 4 else "low")


# ═══════════════════════════ ЧЕРТЫ (лучшая / худшая сторона) ═══════════════════════════
@dataclass(frozen=True)
class Trait:
    key: str                       # ключи перевода: trait.<key>.best / .good / .bad / .worst
    metric: str
    higher_better: bool = True
    pct: bool = False              # показывать значение как проценты
    decimals: int = 0
    scale: float = 1.0
    weight: float = 1.0            # «заметность» черты: редкие/интересные чуть выше
    min_sd: float = 0.01           # минимальный разброс по лобби (чтобы шум не раздувал z)


TRAITS: List[Trait] = [
    Trait("aim", "hs", pct=True, min_sd=0.02, weight=1.1),
    Trait("entry", "entry", pct=True, min_sd=0.04, weight=1.1),
    Trait("early_death", "fd", higher_better=False, pct=True, min_sd=0.015),
    Trait("attack", "atk", decimals=0, min_sd=8.0),
    Trait("defense", "def", decimals=0, min_sd=8.0),
    Trait("damage", "adr", decimals=0, min_sd=8.0),
    Trait("impact", "acs", decimals=0, min_sd=12.0, weight=0.9),
    Trait("kd", "kd", decimals=2, min_sd=0.08, weight=0.9),
    Trait("survival", "surv", pct=True, min_sd=0.03),
    Trait("kast", "kast", pct=True, min_sd=0.03),
    Trait("teamplay", "apr", decimals=2, min_sd=0.03, weight=1.05),
    Trait("trades", "trade", decimals=2, min_sd=0.02, weight=1.1),
    Trait("supported", "traded", pct=True, min_sd=0.04, weight=1.1),
    Trait("multikills", "mk", decimals=2, min_sd=0.01, weight=1.15),
    Trait("consistency", "cv", higher_better=False, pct=True, min_sd=0.03, weight=1.1),
    Trait("winning", "wr", pct=True, min_sd=0.08, weight=0.9),
    Trait("clutch_games", "close_wr", pct=True, min_sd=0.1, weight=1.15),
    Trait("trend", "trend", pct=True, min_sd=0.05, weight=1.2),
    Trait("session", "session", pct=True, min_sd=0.05, weight=1.2),
    Trait("agent", "agent_mastery", decimals=2, min_sd=0.05, weight=1.2),
    Trait("map", "map_mastery", decimals=2, min_sd=0.05, weight=1.15),
    Trait("dominance", "dom", decimals=2, min_sd=0.04, weight=1.0),
    Trait("objective", "spike", decimals=2, min_sd=0.01, weight=1.15),
]
TRAIT_KEYS = [t.key for t in TRAITS]
TRAIT_VARIANTS = ("best", "good", "bad", "worst")


@dataclass
class TraitHit:
    key: str
    side: str                      # best | worst
    variant: str                   # best | good | bad | worst  → ключ перевода trait.<key>.<variant>
    z: float
    value: Optional[float]
    lobby_avg: Optional[float]
    rank: int                      # место среди игроков лобби (1 — лучший по этой черте)
    of: int
    value_text: str = ""
    avg_text: str = ""

    @property
    def label_key(self) -> str:
        return f"trait.{self.key}.{self.variant}"


def _fmt(t: Trait, v: Optional[float]) -> str:
    if v is None:
        return "—"
    if t.pct:
        return f"{v * 100:.0f}%"
    return f"{v:.{t.decimals}f}"


def assign_traits(metrics: Dict[str, dict], reliab: Dict[str, float]) -> Dict[str, tuple]:
    """→ {puuid: (best TraitHit | None, worst TraitHit | None)} с ограничением повторов черт."""
    # значения по лобби
    stats: Dict[str, tuple] = {}
    for t in TRAITS:
        vals = [(s, m.get(t.metric)) for s, m in metrics.items() if m.get(t.metric) is not None]
        if len(vals) < 3:
            continue
        xs = [v for _, v in vals]
        mu = sum(xs) / len(xs)
        sd = max(math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs)), t.min_sd)
        order = sorted(vals, key=lambda sv: sv[1], reverse=t.higher_better)
        rank = {s: i + 1 for i, (s, _) in enumerate(order)}
        stats[t.key] = (t, mu, sd, rank, len(vals))

    cands = []     # (salience, puuid, trait, z)
    for key, (t, mu, sd, rank, n) in stats.items():
        for s, m in metrics.items():
            v = m.get(t.metric)
            if v is None:
                continue
            z = (v - mu) / sd * (1 if t.higher_better else -1)
            cands.append((z * t.weight * reliab.get(s, 1.0), s, t, z))

    def hit(s, t, z, side):
        _, mu, sd, rank, n = stats[t.key]
        r = rank[s]
        v = metrics[s].get(t.metric)
        if side == "best":
            variant = "best" if r == 1 else "good"
        else:
            variant = "worst" if r == n else "bad"
        return TraitHit(t.key, side, variant, z, v, mu, r, n, _fmt(t, v), _fmt(t, mu))

    result: Dict[str, list] = {s: [None, None] for s in metrics}
    for side, idx, sign in (("best", 0, 1), ("worst", 1, -1)):
        pool = sorted((c for c in cands if sign * c[0] > 0.30), key=lambda c: -sign * c[0])
        used: Dict[str, int] = {}
        for limit in (1, 2, 3):
            for sal, s, t, z in pool:
                if result[s][idx] is not None or used.get(t.key, 0) >= limit:
                    continue
                result[s][idx] = hit(s, t, z, side)
                used[t.key] = used.get(t.key, 0) + 1
    return {s: (v[0], v[1]) for s, v in result.items()}


# ═══════════════════════════ СМУРФ-ДЕТЕКТОР ═══════════════════════════
@dataclass
class SmurfResult:
    p: float                                       # вероятность 0..1
    signals: List[tuple] = field(default_factory=list)   # (ключ перевода, значение-текст, вес)


def smurf_estimate(p: Player, metrics: Optional[dict] = None, cfg: Optional[Cfg] = None) -> SmurfResult:
    """Логистическая модель из «улик». База ≈ 4%; каждая улика сдвигает логит. Улики по результатам матчей
    ослабляются, если матчей мало. Это эвристика, а не вердикт."""
    cfg = cfg or Cfg()
    m = metrics if metrics is not None else compute_metrics(p, cfg)
    n = m.get("n", 0) or 0
    conf = min(1.0, n / 8.0)
    z = -3.2
    sig: List[tuple] = []

    def add(key, text, w):
        nonlocal z
        z += w
        if abs(w) >= 0.25:
            sig.append((key, text, w))

    # 1. уровень аккаунта
    lv = p.level
    if lv is not None:
        w = (1.5 if lv < 30 else 1.0 if lv < 50 else 0.45 if lv < 75 else -0.2 if lv < 150 else -0.9 if lv < 250 else -1.3)
        if p.tier >= 15 and lv < 60:
            w += 0.4                                # высокий ранг при малом уровне — особенно подозрительно
        add("smurf.sig.level", str(lv), w)

    # 2. опыт в соревновательном: игр всего / актов
    if p.total_games is not None:
        g = p.total_games
        w = 0.0
        if g <= 40:
            w = 1.3 if p.tier >= 12 else 0.6
        elif g <= 90:
            w = 0.5 if p.tier >= 12 else 0.2
        elif g >= 600:
            w = -1.0
        elif g >= 300:
            w = -0.6
        add("smurf.sig.games", str(g), w)
    if p.acts_played is not None:
        w = 0.35 if p.acts_played <= 1 else (-0.5 if p.acts_played >= 6 else 0.0)
        add("smurf.sig.acts", str(p.acts_played), w)

    # 3. доминирование над лобби (не зависит от ранга)
    dom = m.get("dom")
    if dom is not None:
        w = (1.9 if dom >= 1.35 else 1.3 if dom >= 1.25 else 0.7 if dom >= 1.15 else 0.0 if dom >= 0.97 else -0.6) * conf
        add("smurf.sig.dominance", f"{dom:.2f}×", w)
    top1 = [r for r in p.records if r.placement == 1]
    if n >= 5:
        share = len(top1) / n
        if share >= 0.45:
            add("smurf.sig.top1", f"{share * 100:.0f}%", 1.0 * conf)
    # 4. K/D, HS, винрейт
    kd = m.get("kd")
    if kd is not None:
        add("smurf.sig.kd", f"{kd:.2f}", (1.0 if kd >= 1.5 else 0.5 if kd >= 1.3 else -0.4 if kd < 1.0 else 0.0) * conf)
    hs = m.get("hs")
    if hs is not None and hs >= 0.30:
        add("smurf.sig.hs", f"{hs * 100:.0f}%", 0.5 * conf)
    wr = m.get("wr")
    if wr is not None and n >= 6:
        add("smurf.sig.wr", f"{wr * 100:.0f}%",
            (1.0 if wr >= 0.70 else 0.5 if wr >= 0.62 else -0.6 if wr <= 0.40 else 0.0) * conf)
    # 5. без ранга, но уже доминирует
    if p.tier < 3 and dom is not None and dom >= 1.2:
        add("smurf.sig.unranked", "—", 0.8 * conf)

    z = max(min(z, 8.0), -8.0)
    sig.sort(key=lambda x: -x[2])
    return SmurfResult(sigmoid(z), sig)


def smurf_probability(p: Player) -> float:
    return smurf_estimate(p).p


# ═══════════════════════════ СИЛА ИГРОКА (THREAT) ═══════════════════════════
THREAT_CLASSES = [(85.0, "elite"), (68.0, "dangerous"), (45.0, "average"), (30.0, "below"), (-1.0, "weak")]


def threat_class(score: float) -> str:
    for lim, name in THREAT_CLASSES:
        if score >= lim:
            return name
    return "weak"


@dataclass
class PlayerProfile:
    puuid: str
    name: str
    team: str
    agent: str
    agent_uuid: str
    is_me: bool
    n: int                                   # матчей в анализе
    reliability: str                         # low | medium | high
    threat: float                            # 0..100
    threat_class: str
    lobby_rank: int                          # место по силе в лобби (1 — самый сильный)
    parts: Dict[str, float]                  # вклад факторов в z: rank, form, agent, map, session
    best: Optional[TraitHit]
    worst: Optional[TraitHit]
    smurf: SmurfResult
    style: str                               # ключ перевода style.<..>
    radar: List[tuple]                       # [(ключ оси, 0..1)]
    series: List[tuple]                      # [(acs, won)] от старых к новым — для спарклайна
    metrics: dict
    rating: float = 0.0


@dataclass
class LobbyAnalysis:
    profiles: Dict[str, PlayerProfile]
    order: List[str]                         # puuid по убыванию силы
    avg_threat: Dict[str, float]             # team -> средняя сила
    smurf_count: int
    weakest_ally: Optional[str]
    strongest: Optional[str]


def _z(v, mu, sd):
    return 0.0 if v is None else (v - mu) / sd


def _style(m: dict, lob: Dict[str, tuple]) -> str:
    """Стиль игры по профилю (z-оценки относительно лобби)."""
    if not m.get("n"):
        return "style.unknown"
    z = lambda k: _z(m.get(k), *lob[k]) if k in lob else 0.0
    if z("fk") > 0.7 and z("entry") > -0.2:
        return "style.entry"
    if z("fd") > 0.8 and z("surv") < -0.3:
        return "style.aggressive"
    if z("apr") > 0.6 and z("acs") < 0.4:
        return "style.support"
    if z("acs") > 0.8:
        return "style.fragger"
    if z("surv") > 0.7 and z("kpr") < 0.3:
        return "style.passive"
    return "style.balanced"


def analyze_players(lobby: Lobby, cfg: Optional[Cfg] = None, now_ms: Optional[float] = None) -> LobbyAnalysis:
    cfg = cfg or Cfg()
    _MAP.v = lobby.map_url
    players = lobby.players
    metrics = {p.puuid: compute_metrics(p, cfg, now_ms) for p in players}

    ranked = [tier_points(p.tier, p.rr) for p in players if p.tier >= 3]
    lobby_mean_pts = sum(ranked) / len(ranked) if ranked else 1000.0
    models = {p.puuid: build_model(p, lobby.map_url, lobby_mean_pts, cfg) for p in players}
    ratings = [m.rating for m in models.values()]
    r_mean = sum(ratings) / len(ratings) if ratings else 0.0
    base_mean = sum(m.base for m in models.values()) / max(len(models), 1)
    scale = cfg.rank_sigma                                   # 110 очков ≈ 1 «сигма» силы

    # сила = z из рейтинга + поправка на сессию, с уменьшением при малой выборке
    rel = {s: min(1.0, (m.get("n") or 0) / 6.0) for s, m in metrics.items()}
    raw: Dict[str, float] = {}
    parts: Dict[str, Dict[str, float]] = {}
    for s, mod in models.items():
        sess = metrics[s].get("session")
        sess_z = max(-0.4, min(0.4, (sess or 0.0) * 1.5)) if metrics[s].get("session_n", 0) >= 2 else 0.0
        rank_z = (mod.base - base_mean) / scale
        form_z = cfg.perf_points_per_sigma * mod.form_overall / scale
        agent_z = cfg.perf_points_per_sigma * mod.agent_delta / scale
        map_z = cfg.perf_points_per_sigma * mod.map_delta / scale
        shrink_k = 0.55 + 0.45 * rel[s]                       # мало матчей → меньше доверия к форме
        z = rank_z + shrink_k * (form_z + agent_z + map_z) + sess_z
        raw[s] = z
        parts[s] = {"rank": rank_z, "form": shrink_k * form_z, "agent": shrink_k * agent_z,
                    "map": shrink_k * map_z, "session": sess_z}
    zs = list(raw.values())
    z_mean = sum(zs) / len(zs) if zs else 0.0               # центрируем: средний игрок лобби ≈ 50
    threat = {s: 100.0 * sigmoid(1.15 * (z - z_mean)) for s, z in raw.items()}
    order = sorted(threat, key=lambda s: -threat[s])
    rank_of = {s: i + 1 for i, s in enumerate(order)}

    traits = assign_traits(metrics, rel)

    # статистика лобби для стиля и радара
    lob: Dict[str, tuple] = {}
    for k in ("fk", "fd", "entry", "surv", "apr", "acs", "kpr", "hs", "kast", "trade", "cv"):
        xs = [m[k] for m in metrics.values() if m.get(k) is not None]
        if len(xs) >= 2:
            mu = sum(xs) / len(xs)
            sd = max(math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs)), 1e-6)
            lob[k] = (mu, sd)

    def sq(v):            # z → 0..1
        return 0.5 + 0.5 * math.tanh(v / 1.6)

    profiles: Dict[str, PlayerProfile] = {}
    for p in players:
        m = metrics[p.puuid]
        zf = lambda k: _z(m.get(k), *lob[k]) if (k in lob and m.get(k) is not None) else 0.0
        if m.get("n"):
            radar = [("aim", sq(zf("hs"))), ("entry", sq(zf("entry"))), ("survival", sq(zf("surv"))),
                     ("impact", sq(zf("acs"))), ("teamplay", sq((zf("apr") + zf("kast") + zf("trade")) / 3)),
                     ("consistency", sq(-zf("cv")) if m.get("cv") is not None else 0.5)]
        else:
            radar = [(k, 0.5) for k in ("aim", "entry", "survival", "impact", "teamplay", "consistency")]
        recs = sorted(p.records, key=lambda r: r.started_ms)
        series = [(r.score / max(r.rounds, 1), r.won) for r in recs]
        best, worst = traits.get(p.puuid, (None, None))
        profiles[p.puuid] = PlayerProfile(
            puuid=p.puuid, name=p.name, team=p.team, agent=p.agent, agent_uuid=p.agent_uuid, is_me=p.is_me,
            n=m.get("n", 0) or 0, reliability=reliability(m.get("n", 0) or 0),
            threat=threat[p.puuid], threat_class=threat_class(threat[p.puuid]), lobby_rank=rank_of[p.puuid],
            parts=parts[p.puuid], best=best, worst=worst, smurf=smurf_estimate(p, m, cfg),
            style=_style(m, lob), radar=radar, series=series, metrics=m, rating=models[p.puuid].rating)

    avg = {}
    for team in {p.team for p in players}:
        xs = [threat[p.puuid] for p in players if p.team == team]
        avg[team] = sum(xs) / len(xs)
    allies = [p.puuid for p in players if p.team == "ally"]
    return LobbyAnalysis(
        profiles=profiles, order=order, avg_threat=avg,
        smurf_count=sum(1 for pr in profiles.values() if pr.smurf.p >= 0.6 and not pr.is_me),
        weakest_ally=min(allies, key=lambda s: threat[s]) if len(allies) >= 2 else None,
        strongest=order[0] if order else None)
