"""Прогноз исхода матча (без обучения, только математика).

Что считается
-------------
1. Рейтинг каждого игрока (`skill.build_model`): ранг + форма с затуханием, сжатием и поправками на агента/карту.
2. Разность средних рейтингов команд d ~ N(μ, s²) (s — суммарная неопределённость рейтингов) + штрафы композиции.
3. Вероятность раунда → ТОЧНОЕ распределение счёта динамическим программированием по раундам
   (13+овертайм до +2 или Swiftplay до 5 с решающим раундом).
4. Неопределённость по d и по стороне карты интегрируется КВАДРАТУРОЙ (≈45 узлов), а не тысячами Monte-Carlo
   прогонов: результат детерминирован и считается за десятки миллисекунд.
5. Monte-Carlo остался только для индивидуальных прогнозов (ACS, K/D, MVP), и он лёгкий: счёт сэмплируется из
   уже посчитанных распределений.
6. `explain` раскладывает прогноз на причины (что и на сколько процентных пунктов сдвинуло шансы).
"""
from __future__ import annotations

import math
import random
from bisect import bisect_left
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

from valinfo.analytics.lobby import Lobby
from valinfo.analytics.skill import Cfg, PlayerModel, build_model, comp_adjust, logit, shrink, sigmoid, tier_points
from valinfo.modes import BOMB_13, SWIFT_5, ruleset_of


# ═══════════════════════════ ПРАВИЛА РЕЖИМА И СЧЁТ ═══════════════════════════
@dataclass(frozen=True)
class Ruleset:
    key: str
    win: int          # раундов для победы
    half: int         # раундов до смены сторон
    overtime: str     # "win_by_two" (13) или "sudden" (Swiftplay: решающий раунд)


RULESETS = {
    BOMB_13: Ruleset(BOMB_13, 13, 12, "win_by_two"),
    SWIFT_5: Ruleset(SWIFT_5, 5, 4, "sudden"),
}


def _regulation(p_att: float, p_def: float, start_attack: bool, rs: Ruleset):
    """Регулярные раунды (до смены сторон ×2). Возвращает (финальные счёта, масса в ничьей win-1 : win-1)."""
    win, half = rs.win, rs.half
    cur = [1.0]                                   # cur[a] — вероятность счёта a:(n-a) после n раундов
    final: Dict[Tuple[int, int], float] = {}
    for n in range(2 * half):
        attacking = start_attack if n < half else (not start_attack)
        p = p_att if attacking else p_def
        q = 1.0 - p
        nxt = [0.0] * (n + 2)
        for a in range(n + 1):
            pr = cur[a]
            if pr == 0.0:
                continue
            b = n - a
            if a + 1 == win:
                final[(win, b)] = final.get((win, b), 0.0) + pr * p
            else:
                nxt[a + 1] += pr * p
            if b + 1 == win:
                final[(a, win)] = final.get((a, win), 0.0) + pr * q
            else:
                nxt[a] += pr * q
        cur = nxt
    return final, (cur[win - 1] if win - 1 < len(cur) else 0.0)


def score_distribution(p_att: float, p_def: float, start_attack: bool, rs: Ruleset, ot_terms: int = 10) -> dict:
    """Полное распределение итогового счёта. Овертайм (13): стороны чередуются каждый раунд, победа при +2 →
    пара раундов = 1 атака + 1 защита, выиграть пару с вероятностью x, проиграть y, «поделить» s."""
    dist, tie = _regulation(p_att, p_def, start_attack, rs)
    w = rs.win
    if rs.overtime == "sudden":
        p_dec = 0.5 * (p_att + p_def)             # сторона решающего раунда неизвестна — берём среднее
        dist[(w, w - 1)] = dist.get((w, w - 1), 0.0) + tie * p_dec
        dist[(w - 1, w)] = dist.get((w - 1, w), 0.0) + tie * (1 - p_dec)
        return dist
    x = p_att * p_def
    y = (1 - p_att) * (1 - p_def)
    s = 1 - x - y
    t = tie
    for j in range(ot_terms):
        k1, k2 = (w + 1 + j, w - 1 + j), (w - 1 + j, w + 1 + j)
        dist[k1] = dist.get(k1, 0.0) + t * x
        dist[k2] = dist.get(k2, 0.0) + t * y
        t *= s
    if t > 0 and (x + y) > 0:
        k1, k2 = (w + 1 + ot_terms, w - 1 + ot_terms), (w - 1 + ot_terms, w + 1 + ot_terms)
        dist[k1] = dist.get(k1, 0.0) + t * x / (x + y)
        dist[k2] = dist.get(k2, 0.0) + t * y / (x + y)
    return dist


def win_prob_of(dist: dict) -> float:
    return sum(v for (a, b), v in dist.items() if a > b)


def estimate_attack_wr(samples: list, cfg: Cfg) -> Tuple[float, float, int]:
    """Винрейт атаки на карте из реальных матчей в историях игроков (сжатие к 50%)."""
    atk = sum(a for a, _ in samples)
    tot = sum(t for _, t in samples)
    if tot <= 0:
        return 0.5, 0.5 / math.sqrt(cfg.map_side_k_rounds), 0
    wr = shrink(atk / tot, tot, 0.5, cfg.map_side_k_rounds)
    se = 0.5 / math.sqrt(tot + cfg.map_side_k_rounds)
    return min(max(wr, 0.40), 0.60), se, tot


def collect_side_samples(lobby: Lobby) -> list:
    """(раундов выиграли атакующие, всего раундов) по УНИКАЛЬНЫМ матчам на этой карте и в этих правилах."""
    seen, out = set(), []
    for p in lobby.players:
        for r in p.records:
            if (r.mid in seen or not r.m_rounds or r.map_url != lobby.map_url
                    or ruleset_of(r.queue) != lobby.mode.ruleset):
                continue
            seen.add(r.mid)
            out.append((r.m_atk_won, r.m_rounds))
    return out


def _round_probs(d: float, L: float, cfg: Cfg) -> Tuple[float, float]:
    p0 = 1.0 / (1.0 + 10 ** (-d / cfg.round_elo_scale))
    return sigmoid(logit(p0) + L), sigmoid(logit(p0) - L)


def win_at(d: float, att_wr: float, rs: Ruleset, cfg: Cfg) -> float:
    """Шанс победы при ТОЧНО известной разнице рейтингов d (без интегрирования неопределённости)."""
    pa, pd_ = _round_probs(d, logit(att_wr), cfg)
    starts = (True, False) if cfg.start_attack is None else (cfg.start_attack,)
    return sum(win_prob_of(score_distribution(pa, pd_, s, rs)) for s in starts) / len(starts)


# ═══════════════════════════ РЕЗУЛЬТАТ ═══════════════════════════
@dataclass
class PlayerForecast:
    m: PlayerModel
    exp_acs: float = 0.0
    exp_kills: float = 0.0
    exp_deaths: float = 0.0
    p_mvp_lobby: float = 0.0
    p_best_team: float = 0.0
    p_worst_team: float = 0.0


@dataclass
class Reason:
    kind: str                       # ключ шаблона перевода: reason.<kind>
    impact: Optional[float] = None  # вклад в шанс победы вашей команды, доля (0.042 = +4.2 п.п.); None — справочно
    params: dict = field(default_factory=dict)


@dataclass
class Forecast:
    ruleset: Ruleset
    win_prob: float
    win_lo: float
    win_hi: float
    p_round_neutral: float
    att_wr: float
    att_rounds_seen: int
    dist: Dict[Tuple[int, int], float]
    ally: List[PlayerForecast]
    enemy: List[PlayerForecast]
    ally_rating: float
    enemy_rating: float
    ally_comp: Tuple[float, List[str]]
    enemy_comp: Tuple[float, List[str]]
    avg_n_eff: float
    mu_d: float = 0.0
    sd_d: float = 0.0
    reasons: List[Reason] = field(default_factory=list)

    # ── производные величины для UI ──
    @property
    def p_overtime(self) -> float:
        w = self.ruleset.win
        if self.ruleset.overtime == "sudden":
            return sum(v for (a, b), v in self.dist.items() if a + b == 2 * (w - 1) + 1)
        return sum(v for (a, b), v in self.dist.items() if max(a, b) > w)

    @property
    def exp_rounds(self) -> float:
        return sum(v * (a + b) for (a, b), v in self.dist.items())

    @property
    def exp_score(self) -> Tuple[float, float]:
        return (sum(v * a for (a, b), v in self.dist.items()), sum(v * b for (a, b), v in self.dist.items()))

    def stomp(self, ally_side: bool) -> float:
        w = self.ruleset.win
        lim = int(w * 0.4)
        return sum(v for (a, b), v in self.dist.items()
                   if (a == w and b <= lim if ally_side else b == w and a <= lim))

    def top_scores(self, n: int = 6) -> list:
        return sorted(self.dist.items(), key=lambda kv: -kv[1])[:n]

    @property
    def confidence(self) -> str:
        if self.avg_n_eff < 3:
            return "low"
        return "medium" if self.avg_n_eff < 6 else "high"


# ═══════════════════════════ ОСНОВНОЙ РАСЧЁТ ═══════════════════════════
def _z_nodes(k: int) -> List[float]:
    nd = NormalDist()
    return [nd.inv_cdf((i + 0.5) / k) for i in range(k)]


def analyze(lobby: Lobby, cfg: Optional[Cfg] = None) -> Forecast:
    cfg = cfg or Cfg()
    key = lobby.mode.ruleset
    if key not in RULESETS:
        raise ValueError(f"No ruleset for mode {lobby.mode.queue!r}")
    rs = RULESETS[key]

    ranked = [tier_points(p.tier, p.rr) for p in lobby.players if p.tier >= 3]
    lobby_mean = sum(ranked) / len(ranked) if ranked else 1000.0
    models = [build_model(p, lobby.map_url, lobby_mean, cfg) for p in lobby.players]
    ally = [m for m in models if m.p.team == "ally"]
    enemy = [m for m in models if m.p.team == "enemy"]
    if not ally or not enemy:
        raise ValueError("Need both teams")
    na, nb = len(ally), len(enemy)

    comp_a = comp_adjust([m.p.role for m in ally], cfg) if na == 5 else (0.0, [])
    comp_b = comp_adjust([m.p.role for m in enemy], cfg) if nb == 5 else (0.0, [])
    att_wr, att_se, att_rounds = estimate_attack_wr(collect_side_samples(lobby), cfg)

    mean_a = sum(m.rating for m in ally) / na
    mean_b = sum(m.rating for m in enemy) / nb
    mu_d = (mean_a + comp_a[0]) - (mean_b + comp_b[0])
    sd_d = math.sqrt(sum(m.sigma ** 2 for m in ally) / na ** 2 + sum(m.sigma ** 2 for m in enemy) / nb ** 2)

    # ── квадратура по d (рейтинг) и по L (сторона карты) ──
    starts = (True, False) if cfg.start_attack is None else (cfg.start_attack,)
    zd, zl = _z_nodes(cfg.quad_nodes_rating), _z_nodes(cfg.quad_nodes_side)
    d_vals = [mu_d + sd_d * z for z in zd]
    l_vals = [logit(min(max(att_wr + att_se * z, 0.40), 0.60)) for z in zl]
    w_each = 1.0 / (len(zl) * len(starts))

    agg: Dict[Tuple[int, int], float] = {}
    node_dists = []                                # распределение счёта на каждом узле d (для выборки в MC)
    for d in d_vals:
        node: Dict[Tuple[int, int], float] = {}
        for L in l_vals:
            pa, pd_ = _round_probs(d, L, cfg)
            for s in starts:
                for k, v in score_distribution(pa, pd_, s, rs).items():
                    node[k] = node.get(k, 0.0) + v * w_each
        node_dists.append(node)
        for k, v in node.items():
            agg[k] = agg.get(k, 0.0) + v / len(d_vals)
    win_prob = win_prob_of(agg)
    win_lo = win_at(mu_d - 1.645 * sd_d, att_wr, rs, cfg)
    win_hi = win_at(mu_d + 1.645 * sd_d, att_wr, rs, cfg)

    # ── Monte-Carlo только для индивидуальных прогнозов ──
    node_tables = []
    for node in node_dists:
        keys = list(node.keys())
        cum, acc = [], 0.0
        for k in keys:
            acc += node[k]
            cum.append(acc)
        node_tables.append((keys, cum, acc))

    rng = random.Random(cfg.seed)
    N = cfg.mc_samples
    allm = ally + enemy
    n_all = len(allm)
    acc_acs, acc_k, acc_d = [0.0] * n_all, [0.0] * n_all, [0.0] * n_all
    top_lobby, top_team, bot_team = [0] * n_all, [0] * n_all, [0] * n_all

    for _ in range(N):
        Ra = [rng.gauss(m.rating, m.sigma) for m in ally]
        Rb = [rng.gauss(m.rating, m.sigma) for m in enemy]
        ma, mb = sum(Ra) / na, sum(Rb) / nb
        d = (ma + comp_a[0]) - (mb + comp_b[0])
        idx = min(max(bisect_left(d_vals, d), 0), len(d_vals) - 1)
        keys, cum, total = node_tables[idx]
        a, b = keys[min(bisect_left(cum, rng.random() * total), len(keys) - 1)]
        rounds = a + b
        delta = (a - b) / rounds
        Rs = Ra + Rb
        sims = []
        for i, m in enumerate(allm):
            is_ally = i < na
            opp_avg = mb if is_ally else ma
            sign = 1 if is_ally else -1
            mult = 1 + cfg.opp_slope * (Rs[i] - opp_avg) + cfg.acs_score_slope * delta * sign
            mult = min(max(mult, 0.6), 1.5)
            mu = max(40.0, rng.gauss(m.acs, m.acs_mu_sd))
            acs = max(0.0, rng.gauss(mu * mult, m.acs_sd))
            sims.append(acs)
            acc_acs[i] += acs
            acc_k[i] += m.kpr * mult * rounds
            acc_d[i] += m.dpr / math.sqrt(mult) * rounds
        top_lobby[max(range(n_all), key=sims.__getitem__)] += 1
        ta, tb = sims[:na], sims[na:]
        top_team[max(range(na), key=ta.__getitem__)] += 1
        top_team[na + max(range(nb), key=tb.__getitem__)] += 1
        bot_team[min(range(na), key=ta.__getitem__)] += 1
        bot_team[na + min(range(nb), key=tb.__getitem__)] += 1

    pfs = [PlayerForecast(m=m, exp_acs=acc_acs[i] / N, exp_kills=acc_k[i] / N, exp_deaths=acc_d[i] / N,
                          p_mvp_lobby=top_lobby[i] / N, p_best_team=top_team[i] / N, p_worst_team=bot_team[i] / N)
           for i, m in enumerate(allm)]
    p0_mean = 1.0 / (1.0 + 10 ** (-mu_d / cfg.round_elo_scale))

    fc = Forecast(
        ruleset=rs, win_prob=win_prob, win_lo=win_lo, win_hi=win_hi, p_round_neutral=p0_mean,
        att_wr=att_wr, att_rounds_seen=att_rounds, dist=agg, ally=pfs[:na], enemy=pfs[na:],
        ally_rating=mean_a, enemy_rating=mean_b, ally_comp=comp_a, enemy_comp=comp_b,
        avg_n_eff=sum(m.n_eff for m in models) / len(models), mu_d=mu_d, sd_d=sd_d)
    fc.reasons = explain(lobby, fc, models, lobby_mean, cfg)
    return fc


# ═══════════════════════════ ПРИЧИНЫ ПРОГНОЗА ═══════════════════════════
def explain(lobby: Lobby, fc: Forecast, models: List[PlayerModel], lobby_mean: float, cfg: Cfg) -> List[Reason]:
    """Раскладывает прогноз на причины. Вклад каждой — разница шанса победы «как есть» и «без этого фактора»
    (при точных, а не размазанных по неопределённости рейтингах, поэтому цифры — оценка порядка величины)."""
    from valinfo.analytics.profile import smurf_probability     # локальный импорт: profile тоже использует skill

    rs, att = fc.ruleset, fc.att_wr
    base_w = win_at(fc.mu_d, att, rs, cfg)

    def impact(delta_d: float) -> float:
        return base_w - win_at(fc.mu_d - delta_d, att, rs, cfg)

    ally = [m for m in models if m.p.team == "ally"]
    enemy = [m for m in models if m.p.team == "enemy"]
    na, nb = len(ally), len(enemy)
    out: List[Reason] = []

    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    rank_diff = mean([m.base for m in ally]) - mean([m.base for m in enemy])
    form_diff = mean([m.rating - m.base for m in ally]) - mean([m.rating - m.base for m in enemy])
    comp_diff = fc.ally_comp[0] - fc.enemy_comp[0]

    tiers = lambda ms: [m.p.tier for m in ms if m.p.tier >= 3]
    out.append(Reason("rank", impact(rank_diff), {
        "a": mean(tiers(ally)), "b": mean(tiers(enemy)), "a_pts": mean([m.base for m in ally]),
        "b_pts": mean([m.base for m in enemy])}))
    out.append(Reason("form", impact(form_diff), {
        "a": mean([m.form for m in ally]), "b": mean([m.form for m in enemy])}))
    if fc.ally_comp[1] or fc.enemy_comp[1]:
        out.append(Reason("comp", impact(comp_diff), {"ally": list(fc.ally_comp[1]), "enemy": list(fc.enemy_comp[1])}))

    # отдельные игроки: отклонение от среднего по лобби, пересчитанное в вклад в d
    def person(m, team_n, sign):
        dd = sign * (m.rating - lobby_mean) / team_n
        return impact(dd), dd

    def pick(ms, sign, best: bool):
        scored = [(person(m, len(ms), sign)[0], m) for m in ms if m.n_eff > 0 or m.p.tier >= 3]
        if not scored:
            return None
        imp, m = (max if best else min)(scored, key=lambda x: x[0])
        return (imp, m) if abs(imp) >= 0.01 else None

    for kind, ms, sign, best in (("star_ally", ally, 1, True), ("star_enemy", enemy, -1, False),
                                 ("weak_ally", ally, 1, False), ("weak_enemy", enemy, -1, True)):
        hit = pick(ms, sign, best)
        if hit:
            imp, m = hit
            if (kind.startswith("star") and ((imp > 0) == (sign > 0))) or (kind.startswith("weak") and ((imp < 0) == (sign > 0))):
                out.append(Reason(kind, imp, {"name": m.p.name, "agent": m.p.agent, "delta": m.rating - lobby_mean}))

    # справочные
    for m in sorted(models, key=lambda m: m.p.team != "ally")[:]:
        if m.n_eff >= 4 and m.n_agent < 0.9 and m.p.agent_uuid and not m.p.hidden:
            out.append(Reason("unfamiliar", None, {"name": m.p.name, "agent": m.p.agent, "team": m.p.team}))
            if sum(1 for r in out if r.kind == "unfamiliar") >= 2:
                break
    for m in models:
        p_s = smurf_probability(m.p)
        if p_s >= 0.60 and not m.p.is_me:
            out.append(Reason("smurf", None, {"name": m.p.name, "team": m.p.team, "p": p_s}))
    out.append(Reason("map_side", None, {"map": lobby.map_name, "wr": fc.att_wr, "rounds": fc.att_rounds_seen}))
    if abs(fc.win_prob - 0.5) < 0.03:
        out.append(Reason("even", None, {}))
    out.append(Reason("confidence", None, {"level": fc.confidence, "n": fc.avg_n_eff}))

    with_imp = sorted([r for r in out if r.impact is not None and abs(r.impact) >= 0.004],
                      key=lambda r: -abs(r.impact))
    info = [r for r in out if r.impact is None]
    return with_imp + info
