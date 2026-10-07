"""Общая математика оценки игрока (используется прогнозом и анализом игроков).

Идея: рейтинг игрока = ранг (в очках) + форма из последних матчей, где форма считается с затуханием по давности,
байесовским сжатием к приору роли и поправками на текущего агента и карту. Подробности — в docstring `ctx_estimate`.
Все приоры/веса — разумные оценки, а не откалиброванные на датасете значения: их можно крутить в `Cfg`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


@dataclass
class Cfg:
    # --- история ---
    history_matches: int = 20            # максимум матчей на игрока
    half_life_matches: float = 8.0       # период полураспада веса матча (в матчах)

    # --- байесовское сжатие (чем больше k, тем сильнее тянем к приору) ---
    k_form: float = 6.0
    k_agent: float = 5.0
    k_map: float = 8.0
    map_weight: float = 0.5
    agent_unfamiliar_pen_f: float = 0.15

    # --- рейтинг ---
    perf_points_per_sigma: float = 70.0
    rank_sigma: float = 110.0
    unranked_sigma: float = 260.0
    round_elo_scale: float = 1900.0      # 100 очков разницы средних рейтингов ≈ 53% раунда

    # --- композиция команды (в очках среднего рейтинга) ---
    comp_no_controller: float = -35.0
    comp_no_initiator: float = -20.0
    comp_no_duelist: float = -15.0
    comp_no_sentinel: float = -8.0
    comp_triple_duelist: float = -15.0
    comp_double_sentinel_extra: float = -5.0

    # --- карта / стороны ---
    map_side_k_rounds: float = 300.0
    start_attack: Optional[bool] = None  # None = неизвестно → усредняем оба варианта

    # --- индивидуальные прогнозы ---
    acs_score_slope: float = 0.20
    opp_slope: float = 0.0005
    acs_prior_sd: float = 60.0

    # --- численное интегрирование (вместо тяжёлого Monte-Carlo по счёту) ---
    quad_nodes_rating: int = 15          # узлов по неопределённости рейтинга
    quad_nodes_side: int = 3             # узлов по неопределённости винрейта атаки
    mc_samples: int = 1000               # выборок только для индивидуальных прогнозов (ACS/MVP)
    seed: int = 12345


ROLE_PRIORS = {
    "Duelist":    dict(acs=225.0, kpr=0.80, dpr=0.72, adr=150.0),
    "Initiator":  dict(acs=205.0, kpr=0.70, dpr=0.70, adr=138.0),
    "Controller": dict(acs=195.0, kpr=0.65, dpr=0.68, adr=132.0),
    "Sentinel":   dict(acs=190.0, kpr=0.65, dpr=0.68, adr=130.0),
    "Unknown":    dict(acs=205.0, kpr=0.70, dpr=0.70, adr=138.0),
}


def prior_of(role: str) -> dict:
    return ROLE_PRIORS.get(role, ROLE_PRIORS["Unknown"])


def tier_points(t: int, rr: int) -> float:
    """Ранг → числовая шкала (~100 очков на тир). tier: 3 = Iron 1 … 27 = Radiant."""
    if t >= 27:
        return 2400.0 + min(max(rr, 0), 200) * 0.5
    return (t - 3) * 100.0 + max(0, min(rr, 100))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def shrink(obs: float, n: float, prior: float, k: float) -> float:
    """Байесовское сжатие: (n*obs + k*prior)/(n+k)."""
    return (n * obs + k * prior) / (n + k) if (n + k) > 0 else prior


def wmean(items, fn: Callable) -> Tuple[Optional[float], float]:
    """Взвешенное среднее и эффективное число матчей (Kish)."""
    sw = sum(w for w, _ in items)
    if sw <= 0:
        return None, 0.0
    m = sum(w * fn(r) for w, r in items) / sw
    n_eff = sw * sw / sum(w * w for w, _ in items)
    return m, n_eff


def weighted_history(history: list, cfg: Cfg) -> list:
    """[(вес, запись)]: свежие матчи важнее (полураспад) и длинные матчи важнее коротких."""
    hist = sorted(history, key=lambda r: r.started_ms, reverse=True)[: cfg.history_matches]
    out = []
    for i, r in enumerate(hist):
        w = 0.5 ** (i / cfg.half_life_matches) * min(max(r.rounds, 1), 30) / 24.0
        out.append((w, r))
    return out


@dataclass
class CtxEst:
    est: float            # итоговая оценка с учётом агента и карты
    sd: float             # апостериорное sd (в единицах приора)
    n: float              # эффективное число матчей
    overall: float        # общая (без контекста)
    agent_delta: float    # вклад агента (est_agent - overall)
    map_delta: float      # вклад карты
    n_agent: float
    n_map: float


def ctx_estimate(hw, fn, agent, map_url, prior, cfg: Cfg, pen: float = 0.0) -> CtxEst:
    """Оценка величины с учётом контекста: общая → сжатие к приору; агент → сжатие к общей (минус штраф за
    незнакомость); карта → небольшая поправка от общей."""
    m_all, n_all = wmean(hw, fn)
    overall = prior if m_all is None else shrink(m_all, n_all, prior, cfg.k_form)

    ag = [(w, r) for w, r in hw if agent and r.agent == agent]
    m_a, n_a = wmean(ag, fn)
    agent_prior = overall - (pen if agent else 0.0)
    agent_est = agent_prior if m_a is None else shrink(m_a, n_a, agent_prior, cfg.k_agent)

    mp = [(w, r) for w, r in hw if map_url and r.map_url == map_url]
    m_m, n_m = wmean(mp, fn)
    map_dev = 0.0 if m_m is None else shrink(m_m, n_m, overall, cfg.k_map) - overall

    est = agent_est + cfg.map_weight * map_dev
    return CtxEst(est=est, sd=math.sqrt(cfg.k_form / (n_all + cfg.k_form)), n=n_all, overall=overall,
                  agent_delta=agent_est - overall, map_delta=cfg.map_weight * map_dev, n_agent=n_a, n_map=n_m)


# --- метрики одного матча (нормированы на приор роли) ---
def _rd(r): return max(r.rounds, 1)
def acs_ratio(r): return (r.score / _rd(r)) / prior_of(r.role)["acs"]
def kpr_ratio(r): return (r.kills / _rd(r)) / prior_of(r.role)["kpr"]
def dpr_ratio(r): return (r.deaths / _rd(r)) / prior_of(r.role)["dpr"]


def adr_ratio(r):
    if r.damage <= 0:
        return acs_ratio(r)
    return (r.damage / _rd(r)) / prior_of(r.role)["adr"]


def form_value(r) -> float:
    """Композитный показатель формы за матч (≈ z-score)."""
    p = prior_of(r.role)
    kdd = (r.kills - r.deaths) / _rd(r) - (p["kpr"] - p["dpr"])
    stat = (0.45 * (acs_ratio(r) - 1) / 0.18 + 0.25 * (adr_ratio(r) - 1) / 0.16 + 0.30 * kdd / 0.10)
    win = ((1.0 if r.won else 0.0) - 0.5) / 0.12
    return 0.85 * stat + 0.15 * win


@dataclass
class PlayerModel:
    p: object                       # lobby.Player
    rating: float = 0.0
    base: float = 0.0               # рейтинг только по рангу
    sigma: float = 0.0
    form: float = 0.0
    form_overall: float = 0.0
    agent_delta: float = 0.0
    map_delta: float = 0.0
    n_eff: float = 0.0
    n_agent: float = 0.0
    n_map: float = 0.0
    acs: float = 0.0
    kpr: float = 0.0
    dpr: float = 0.0
    adr: float = 0.0
    hs: float = 0.22
    wr: float = 0.5
    acs_sd: float = 60.0
    acs_mu_sd: float = 15.0


def build_model(p, map_url: str, lobby_mean_pts: float, cfg: Cfg) -> PlayerModel:
    hw = weighted_history(p.records, cfg)
    m = PlayerModel(p=p)
    pr = prior_of(p.role)

    f = ctx_estimate(hw, form_value, p.agent_uuid, map_url, 0.0, cfg, pen=cfg.agent_unfamiliar_pen_f)
    m.form, m.n_eff = f.est, f.n
    m.form_overall, m.agent_delta, m.map_delta = f.overall, f.agent_delta, f.map_delta
    m.n_agent, m.n_map = f.n_agent, f.n_map

    acs_r = ctx_estimate(hw, acs_ratio, p.agent_uuid, map_url, 1.0, cfg, pen=0.03)
    kpr_r = ctx_estimate(hw, kpr_ratio, p.agent_uuid, map_url, 1.0, cfg, pen=0.03)
    dpr_r = ctx_estimate(hw, dpr_ratio, p.agent_uuid, map_url, 1.0, cfg, pen=-0.03)
    adr_r = ctx_estimate(hw, adr_ratio, p.agent_uuid, map_url, 1.0, cfg, pen=0.03)
    m.acs = pr["acs"] * max(acs_r.est, 0.3)
    m.kpr = pr["kpr"] * max(kpr_r.est, 0.2)
    m.dpr = pr["dpr"] * max(dpr_r.est, 0.2)
    m.adr = pr["adr"] * max(adr_r.est, 0.3)
    m.acs_mu_sd = 0.18 * pr["acs"] * acs_r.sd

    vals = [(w, r.score / _rd(r)) for w, r in hw]
    if vals:
        sw = sum(w for w, _ in vals)
        mu = sum(w * x for w, x in vals) / sw
        var = sum(w * (x - mu) ** 2 for w, x in vals) / sw
        m.acs_sd = math.sqrt(shrink(var, f.n, cfg.acs_prior_sd ** 2, 4.0))
    else:
        m.acs_sd = cfg.acs_prior_sd

    wr_obs, n_w = wmean(hw, lambda r: 1.0 if r.won else 0.0)
    m.wr = 0.5 if wr_obs is None else shrink(wr_obs, n_w, 0.5, 8.0)
    num = sum(w * r.hs for w, r in hw)
    den = sum(w * (r.hs + r.bs + r.ls) for w, r in hw)
    m.hs = shrink(num / den, f.n, 0.22, 4.0) if den > 0 else 0.22

    if p.tier >= 3:
        base, sig_rank = tier_points(p.tier, p.rr), cfg.rank_sigma
    else:
        base, sig_rank = lobby_mean_pts, cfg.unranked_sigma
    m.base = base
    m.rating = base + cfg.perf_points_per_sigma * f.est
    m.sigma = math.sqrt(sig_rank ** 2 + (cfg.perf_points_per_sigma * f.sd) ** 2)
    return m


def comp_adjust(roles: List[str], cfg: Cfg) -> Tuple[float, List[str]]:
    """Штраф за дыры в композиции команды. Возвращает (очки, ключи заметок для перевода)."""
    c = {}
    for r in roles:
        c[r] = c.get(r, 0) + 1
    adj, notes = 0.0, []
    if c.get("Controller", 0) == 0:
        adj += cfg.comp_no_controller; notes.append("comp.no_controller")
    if c.get("Initiator", 0) == 0:
        adj += cfg.comp_no_initiator; notes.append("comp.no_initiator")
    if c.get("Duelist", 0) == 0:
        adj += cfg.comp_no_duelist; notes.append("comp.no_duelist")
    if c.get("Sentinel", 0) == 0:
        adj += cfg.comp_no_sentinel; notes.append("comp.no_sentinel")
    if c.get("Duelist", 0) >= 3:
        adj += cfg.comp_triple_duelist; notes.append("comp.triple_duelist")
    if c.get("Sentinel", 0) >= 2:
        adj += cfg.comp_double_sentinel_extra; notes.append("comp.double_sentinel")
    return adj, notes
