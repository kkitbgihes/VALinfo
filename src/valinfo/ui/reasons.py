"""Текст «почему такой прогноз» (без Qt: чистая функция от Reason → строка, удобно тестировать)."""
from __future__ import annotations

from typing import Optional, Tuple

from valinfo.analytics.predictor import Reason
from valinfo.constants import RANKS
from valinfo.i18n import tr

IMPACT_EPS = 0.004


def rank_text(mean_tier: float) -> str:
    """Средний ранг команды, округлённый: 13.4 → 'Gold 1'."""
    t = int(round(mean_tier))
    if t < 3:
        return tr("rank.unranked")
    idx = min((t - 3) // 3, 8)
    return f"{RANKS[idx]} {(t - 3) % 3 + 1}" if idx < 8 else RANKS[8]


def _sign_variant(impact: Optional[float]) -> str:
    if impact is None or abs(impact) < IMPACT_EPS:
        return "even"
    return "pos" if impact > 0 else "neg"


def reason_text(r: Reason) -> Tuple[str, Optional[float]]:
    """→ (готовая фраза, вклад в шанс победы вашей команды или None)."""
    k, pr = r.kind, r.params
    if k == "rank":
        return tr(f"reason.rank.{_sign_variant(r.impact)}", a=rank_text(pr["a"]), b=rank_text(pr["b"])), r.impact
    if k == "form":
        return tr(f"reason.form.{_sign_variant(r.impact)}"), r.impact
    if k == "comp":
        side = pr.get("ally") or []
        other = pr.get("enemy") or []
        parts = []
        if side:
            parts.append(tr("reason.comp.yours", items=", ".join(tr(x) for x in side)))
        if other:
            parts.append(tr("reason.comp.theirs", items=", ".join(tr(x) for x in other)))
        return tr(f"reason.comp.{_sign_variant(r.impact)}", detail=" ".join(parts)), r.impact
    if k in ("star_ally", "star_enemy", "weak_ally", "weak_enemy"):
        return tr(f"reason.{k}", name=pr.get("name", "?"), agent=pr.get("agent", "?")), r.impact
    if k == "unfamiliar":
        return tr(f"reason.unfamiliar.{pr.get('team', 'ally')}", name=pr.get("name", "?"), agent=pr.get("agent", "?")), None
    if k == "smurf":
        return tr(f"reason.smurf.{pr.get('team', 'enemy')}", name=pr.get("name", "?"), p=f"{pr.get('p', 0) * 100:.0f}"), None
    if k == "map_side":
        wr = pr.get("wr", 0.5)
        rounds = pr.get("rounds", 0)
        if rounds < 60:
            return tr("reason.map_side.nodata", map=pr.get("map", "?")), None
        side = tr("reason.side.attack") if wr >= 0.5 else tr("reason.side.defense")
        return tr("reason.map_side", map=pr.get("map", "?"), side=side, p=f"{max(wr, 1 - wr) * 100:.0f}"), None
    if k == "even":
        return tr("reason.even"), None
    if k == "confidence":
        return tr(f"reason.confidence.{pr.get('level', 'low')}", n=f"{pr.get('n', 0):.1f}"), None
    return k, r.impact
