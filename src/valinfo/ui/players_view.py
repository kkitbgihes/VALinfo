"""Вкладка «Игроки»: сила, лучшая/худшая сторона, смурф-детектор, стиль и форма каждого игрока лобби.

Клик по строке открывает подробную карточку: радар навыков, из чего сложена оценка силы, сессия, улики смурфа.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from valinfo.analytics.lobby import ALLY, ENEMY, SOLO
from valinfo.analytics.profile import LobbyAnalysis, PlayerProfile, TraitHit
from valinfo.i18n import tr
from valinfo.ui.analysis_tab import AnalysisTabBase, clear_layout, make_card
from valinfo.ui.charts import MeterBar, RadarChart, Sparkline
from valinfo.ui.theme import c, qcolor
from valinfo.ui.widgets import create_cell, font

ROW_H = 58
COLS = ["agent", "player", "strength", "best", "worst", "smurf", "style", "form"]
COL_W = {0: 108, 2: 160, 3: 212, 4: 212, 5: 66, 6: 126, 7: 92}
CLASS_COLORS = {"elite": "loss", "dangerous": "warn", "average": "mid", "below": "muted", "weak": "muted"}


def _threat_color(cls: str) -> str:
    return getattr(c, CLASS_COLORS.get(cls, "muted"))


def _smurf_color(p: float) -> str:
    return c.loss if p >= 0.6 else c.warn if p >= 0.35 else c.text if p >= 0.15 else c.muted


def _stack(*widgets, margins=(8, 2, 8, 2), spacing=0) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    for x in widgets:
        lay.addWidget(x)
    return w


def _label(text: str, size: int = 9, bold: bool = False, color: Optional[str] = None, wrap: bool = False) -> QLabel:
    l = QLabel(text)
    l.setFont(font(size, bold))
    l.setStyleSheet(f"color: {color or c.text};")
    l.setWordWrap(wrap)
    return l


class DeltaBars(QWidget):
    """Расходящиеся полоски вкладов в силу игрока (влево — минус, вправо — плюс)."""

    ROW = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setMinimumWidth(240)

    def set_rows(self, rows) -> None:
        self.rows = list(rows)
        self.setMinimumHeight(len(self.rows) * self.ROW + 4)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lab_w = 92
        track_x, track_w = lab_w, self.width() - lab_w - 6
        mid = track_x + track_w / 2
        scale = max([abs(v) for _, v in self.rows] + [0.8])
        for i, (label, v) in enumerate(self.rows):
            y = 2 + i * self.ROW
            p.setPen(QColor(c.muted))
            p.setFont(font(9, False))
            p.drawText(QRectF(0, y, lab_w, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(c.border, 0.5))
            p.drawRoundedRect(QRectF(track_x, y + 7, track_w, self.ROW - 14), 3, 3)
            w = abs(v) / scale * (track_w / 2)
            p.setBrush(QColor(c.win if v >= 0 else c.loss))
            x = mid if v >= 0 else mid - w
            p.drawRoundedRect(QRectF(x, y + 7, max(w, 1.5), self.ROW - 14), 3, 3)
            p.setBrush(QColor(c.text))
            p.drawRect(QRectF(mid - 0.5, y + 4, 1, self.ROW - 8))
        p.end()


class PlayersView(AnalysisTabBase):
    KIND = "players"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._selected: Optional[str] = None
        self._row_ids: list = []
        self._table: Optional[QTableWidget] = None
        self._analysis: Optional[LobbyAnalysis] = None
        self._lobby = None
        self._detail_host: Optional[QVBoxLayout] = None

    def precheck(self, payload):
        if payload is None:
            return tr("players.empty.title"), tr("players.empty.sub")
        return None

    # ═════════════ отрисовка ═════════════
    def render(self, lobby, an: LobbyAnalysis, payload: dict) -> None:
        self._lobby, self._analysis = lobby, an
        if self._selected not in an.profiles:
            self._selected = an.strongest
        lay = self._body_lay
        clear_layout(lay)

        lay.addLayout(self._summary(lobby, an))
        lay.addWidget(self._table_card(lobby, an))
        hint = _label(tr("players.click_hint"), 8, color=c.muted)
        lay.addWidget(hint)
        host = QVBoxLayout()
        host.setContentsMargins(0, 0, 0, 0)
        self._detail_host = host
        lay.addLayout(host)
        lay.addStretch()
        self._render_detail()

    def _summary(self, lobby, an: LobbyAnalysis) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        def tile(value, caption, color=None, tip=""):
            f = QFrame()
            f.setObjectName("CardInner")
            l = QVBoxLayout(f)
            l.setContentsMargins(12, 8, 12, 8)
            l.setSpacing(0)
            l.addWidget(_label(value, 13, True, color or c.text))
            l.addWidget(_label(caption, 8, color=c.muted))
            if tip:
                f.setToolTip(tip)
            return f

        if lobby.layout == "teams":
            a, e = an.avg_threat.get(ALLY), an.avg_threat.get(ENEMY)
            if a is not None and e is not None:
                row.addWidget(tile(f"{a:.0f}", tr("players.sum.ally"), c.ally, tr("players.sum.avg_tip")))
                row.addWidget(tile(f"{e:.0f}", tr("players.sum.enemy"), c.enemy, tr("players.sum.avg_tip")))
        if an.strongest:
            pr = an.profiles[an.strongest]
            row.addWidget(tile(pr.name, tr("players.sum.strongest", agent=pr.agent), c.warn))
        if an.weakest_ally and lobby.layout == "teams":
            pr = an.profiles[an.weakest_ally]
            row.addWidget(tile(pr.name, tr("players.sum.weakest", agent=pr.agent), c.muted))
        row.addWidget(tile(str(an.smurf_count), tr("players.sum.smurfs"), c.loss if an.smurf_count else c.muted,
                           tr("players.sum.smurfs_tip")))
        row.addStretch()
        return row

    def _table_card(self, lobby, an: LobbyAnalysis) -> QFrame:
        card = QFrame()
        card.setObjectName("TeamCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        order = {s: i for i, s in enumerate(an.order)}
        team_rank = {ALLY: 0, ENEMY: 1, SOLO: 0}
        profiles = sorted(an.profiles.values(), key=lambda pr: (team_rank.get(pr.team, 2), order[pr.puuid]))
        self._row_ids = [pr.puuid for pr in profiles]

        t = QTableWidget(len(profiles), len(COLS))
        t.setHorizontalHeaderLabels([tr(f"players.col.{k}") for k in COLS])
        h = t.horizontalHeader()
        for col in range(len(COLS)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 1 else QHeaderView.ResizeMode.Fixed)
        for col, w in COL_W.items():
            t.setColumnWidth(col, w)
        t.verticalHeader().setVisible(False)
        t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        t.setShowGrid(False)
        t.setFixedHeight((h.height() or 26) + len(profiles) * ROW_H + 4)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for row, pr in enumerate(profiles):
            t.setRowHeight(row, ROW_H)
            for col, w in enumerate(self._row_widgets(pr)):
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                w.mousePressEvent = lambda e, s=pr.puuid: self._select(s)       # клик по любой ячейке строки
                t.setCellWidget(row, col, w)
                t.setItem(row, col, QTableWidgetItem(""))
        self._table = t
        lay.addWidget(t)
        self._paint_selection()
        return card

    def _row_widgets(self, pr: PlayerProfile) -> list:
        bar = {ALLY: c.ally, ENEMY: c.enemy}.get(pr.team, c.accent)
        w_agent = create_cell(pr.agent, self._icons.agent(pr.agent_uuid), bar_color=bar)
        name_color = c.title if pr.is_me else c.text
        w_name = create_cell(pr.name + (" ◄" if pr.is_me else ""), text_color=name_color, is_bold=pr.is_me)

        col = _threat_color(pr.threat_class)
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        tl.addWidget(_label(tr(f"class.{pr.threat_class}"), 9, True, col))
        tl.addStretch()
        tl.addWidget(_label(f"{pr.threat:.0f}", 9, True))
        meter = MeterBar(pr.threat, col)
        sub = _label(f"#{pr.lobby_rank} · {tr(f'reliab.{pr.reliability}')}", 8, color=c.muted)
        w_strength = _stack(top, meter, sub)
        w_strength.setToolTip(self._strength_tip(pr))

        w_best, w_worst = self._trait_cell(pr.best, c.win), self._trait_cell(pr.worst, c.loss)

        sm = pr.smurf.p
        w_smurf = _stack(_label(f"{sm * 100:.0f}%", 11, True, _smurf_color(sm)), margins=(10, 0, 4, 0))
        w_smurf.setToolTip(self._smurf_tip(pr))

        w_style = _stack(_label(tr(pr.style), 9, False, c.text, wrap=True))
        w_form = _stack(Sparkline(pr.series), margins=(4, 0, 4, 0))
        w_form.setToolTip(tr("players.form_tip", n=pr.n))
        return [w_agent, w_name, w_strength, w_best, w_worst, w_smurf, w_style, w_form]

    @staticmethod
    def _trait_cell(hit: Optional[TraitHit], color: str) -> QWidget:
        if hit is None:
            return _stack(_label("—", 10, False, c.muted))
        top = _label(tr(hit.label_key), 9, True, color, wrap=True)
        sub = _label(tr("players.trait_sub", v=hit.value_text, avg=hit.avg_text, rank=hit.rank, of=hit.of), 8, color=c.muted)
        w = _stack(top, sub)
        w.setToolTip(tr("players.trait_tip", v=hit.value_text, avg=hit.avg_text, rank=hit.rank, of=hit.of))
        return w

    @staticmethod
    def _strength_tip(pr: PlayerProfile) -> str:
        names = {"rank": "rank", "form": "form", "agent": "agent", "map": "map", "session": "session"}
        lines = [f"<b>{tr('players.breakdown')}</b>"]
        for k in names:
            v = pr.parts.get(k, 0.0)
            col = c.win if v > 0.02 else c.loss if v < -0.02 else c.muted
            lines.append(f"{tr(f'part.{k}')}: <span style='color:{col}'>{v:+.2f}</span>")
        return "<br>".join(lines)

    @staticmethod
    def _smurf_tip(pr: PlayerProfile) -> str:
        lines = [f"<b>{tr('players.smurf_title')}</b>"]
        if not pr.smurf.signals:
            lines.append(tr("players.smurf_none"))
        for key, text, w in pr.smurf.signals[:6]:
            col = c.loss if w > 0 else c.win
            lines.append(f"<span style='color:{col}'>{'▲' if w > 0 else '▼'}</span> {tr(key, v=text)}")
        return "<br>".join(lines)

    # ── выбор строки и подробная карточка ──
    def _select(self, puuid: str) -> None:
        if puuid == self._selected:
            return
        self._selected = puuid
        self._paint_selection()
        self._render_detail()

    def _paint_selection(self) -> None:
        if self._table is None:
            return
        sel = QBrush(qcolor(c.accent, 0.14))
        none = QBrush(Qt.GlobalColor.transparent)
        for row, s in enumerate(self._row_ids):
            for col in range(len(COLS)):
                it = self._table.item(row, col)
                if it is not None:
                    it.setBackground(sel if s == self._selected else none)

    def _render_detail(self) -> None:
        host = self._detail_host
        if host is None or self._analysis is None:
            return
        clear_layout(host)
        pr = self._analysis.profiles.get(self._selected or "")
        if pr is None:
            return
        card, lay, _ = make_card(tr("players.detail", name=pr.name, agent=pr.agent))
        body = QHBoxLayout()
        body.setSpacing(18)

        radar = RadarChart([(tr(f"radar.{k}"), v) for k, v in pr.radar])
        body.addWidget(radar, 4)

        mid = QVBoxLayout()
        mid.addWidget(_label(tr("players.breakdown"), 9, True, c.muted))
        bars = DeltaBars()
        bars.set_rows([(tr(f"part.{k}"), pr.parts.get(k, 0.0)) for k in ("rank", "form", "agent", "map", "session")])
        mid.addWidget(bars)
        mid.addWidget(_label(tr("players.breakdown_hint"), 8, color=c.muted, wrap=True))
        mid.addStretch()
        body.addLayout(mid, 4)

        body.addLayout(self._facts(pr), 5)
        lay.addLayout(body)
        host.addWidget(card)

    def _facts(self, pr: PlayerProfile) -> QVBoxLayout:
        m = pr.metrics
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(_label(tr("players.facts"), 9, True, c.muted))

        def fact(text: str, color: Optional[str] = None):
            l = _label(text, 9, False, color or c.text, wrap=True)
            l.setTextFormat(Qt.TextFormat.RichText)
            col.addWidget(l)

        if not m.get("n"):
            fact(tr("players.no_history"), c.muted)
        else:
            fact(tr("players.fact.sample", n=m["n"], rel=tr(f"reliab.{pr.reliability}")))
            if m.get("session_n", 0) >= 2:
                d = m.get("session")
                trend = "" if d is None else f" ({d * 100:+.0f}%)"
                fact(tr("players.fact.session", n=m["session_n"], w=m["session_wins"], acs=f"{m['session_acs']:.0f}") + trend,
                     c.win if (d or 0) > 0.05 else c.loss if (d or 0) < -0.05 else None)
            else:
                fact(tr("players.fact.no_session"), c.muted)
            if m.get("agent_games") is not None:
                fact(tr("players.fact.agent", agent=pr.agent, n=m["agent_games"]),
                     c.loss if m["agent_games"] == 0 else None)
            if m.get("map_games") is not None and self._lobby:
                fact(tr("players.fact.map", map=self._lobby.map_name, n=m["map_games"]))
            if m.get("entry") is not None:
                fact(tr("players.fact.entry", p=f"{m['entry'] * 100:.0f}"))
            if m.get("kast") is not None:
                fact(tr("players.fact.kast", p=f"{m['kast'] * 100:.0f}"))
            if m.get("trend") is not None:
                t = m["trend"]
                fact(tr("players.fact.trend", p=f"{t * 100:+.0f}"), c.win if t > 0.08 else c.loss if t < -0.08 else None)
        fact(tr("players.fact.smurf", p=f"{pr.smurf.p * 100:.0f}"), _smurf_color(pr.smurf.p))
        for key, text, w in pr.smurf.signals[:3]:
            fact(f"&nbsp;&nbsp;{'▲' if w > 0 else '▼'} {tr(key, v=text)}", c.muted)
        col.addStretch()
        return col
