"""Вкладка «Прогноз»: шансы на победу, распределение счёта, прогноз по игрокам и причины прогноза."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QVBoxLayout, QWidget

from valinfo.analytics.lobby import loading_progress
from valinfo.analytics.predictor import Forecast
from valinfo.i18n import tr
from valinfo.modes import FFA, detect_layout, mode_for
from valinfo.ui.analysis_tab import AnalysisTabBase, clear_layout, make_card
from valinfo.ui.charts import HBarList, SplitBar
from valinfo.ui.reasons import reason_text
from valinfo.ui.theme import c, rgba
from valinfo.ui.widgets import create_cell, font

ROW_H = 32
COLS = ["agent", "player", "acs", "kda", "mvp", "best", "worst"]
COL_W = {0: 112, 2: 70, 3: 100, 4: 80, 5: 138, 6: 138}


def _tile(value: str, caption: str, color: Optional[str] = None, tip: str = "") -> QFrame:
    f = QFrame()
    f.setObjectName("CardInner")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(0)
    v = QLabel(value)
    v.setFont(font(15, True))
    v.setStyleSheet(f"color: {color or c.text};")
    cap = QLabel(caption)
    cap.setFont(font(8, False))
    cap.setStyleSheet(f"color: {c.muted};")
    lay.addWidget(v)
    lay.addWidget(cap)
    if tip:
        f.setToolTip(tip)
    return f


def _pct(x: float, d: int = 0) -> str:
    return f"{x * 100:.{d}f}%"


class PredictionsView(AnalysisTabBase):
    KIND = "prediction"

    def precheck(self, payload):
        if payload is None:
            return tr("pred.empty.title"), tr("pred.empty.sub")
        mode = mode_for(payload.get("queue"))
        if mode.ruleset is None:
            return tr("pred.unsupported.title"), tr("pred.unsupported.sub", mode=tr(f"mode.{mode.key}"))
        layout = payload.get("layout") or detect_layout(mode, payload["players"])
        me = payload["me"]
        my_team = next((p["team"] for p in payload["players"] if p["subject"] == me), None)
        if layout == FFA or all(p["team"] == my_team for p in payload["players"]):
            return tr("pred.pregame.title"), tr("pred.pregame.sub")
        done, total = loading_progress(payload)
        if done < total:
            return tr("analysis.calculating"), tr("analysis.loaded", done=done, total=total)
        return None

    # ═════════════ отрисовка ═════════════
    def render(self, lobby, fc: Forecast, payload: dict) -> None:
        lay = self._body_lay
        clear_layout(lay)
        lay.addWidget(self._hero(lobby, fc))
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._scores_card(fc), 5)
        row.addWidget(self._reasons_card(fc), 6)
        lay.addLayout(row)
        lay.addWidget(self._team_card(fc, lobby, ally=True))
        lay.addWidget(self._team_card(fc, lobby, ally=False))
        lay.addStretch()

    def _hero(self, lobby, fc: Forecast) -> QFrame:
        card, lay, _ = make_card(tr("pred.win_chance"))
        top = QHBoxLayout()
        left = QLabel(f'<span style="color:{c.ally};font-size:30px;font-weight:bold;">{_pct(fc.win_prob)}</span>'
                      f'&nbsp;&nbsp;<span style="color:{c.muted};font-size:11px;">{tr("live.your_team")}</span>')
        right = QLabel(f'<span style="color:{c.muted};font-size:11px;">{tr("live.enemy_team")}</span>&nbsp;&nbsp;'
                       f'<span style="color:{c.enemy};font-size:30px;font-weight:bold;">{_pct(1 - fc.win_prob)}</span>')
        left.setTextFormat(Qt.TextFormat.RichText)
        right.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(left)
        top.addStretch()
        top.addWidget(right)
        lay.addLayout(top)
        bar = SplitBar()
        bar.set_value(fc.win_prob, fc.win_lo, fc.win_hi)
        lay.addWidget(bar)

        conf_color = {"high": c.win, "medium": c.mid, "low": c.loss}[fc.confidence]
        info = QLabel(
            f'{tr("pred.interval", lo=_pct(fc.win_lo), hi=_pct(fc.win_hi))} &nbsp;·&nbsp; {tr("pred.confidence")}: '
            f'<b style="color:{conf_color};">{tr(f"conf.{fc.confidence}")}</b> '
            f'<span style="color:{c.muted};">({tr("pred.conf_hint")})</span>')
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(f"color: {c.muted};")
        lay.addWidget(info)

        a, b = fc.exp_score
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        tiles.addWidget(_tile(f"{a:.1f} : {b:.1f}", tr("pred.tile.score"), tip=tr("pred.tile.score_tip")))
        tiles.addWidget(_tile(f"{fc.exp_rounds:.1f}", tr("pred.tile.rounds")))
        tiles.addWidget(_tile(_pct(fc.p_overtime), tr("pred.tile.ot" if fc.ruleset.overtime == "win_by_two" else "pred.tile.decider")))
        tiles.addWidget(_tile(_pct(fc.stomp(True)), tr("pred.tile.stomp_win"), c.win, tr("pred.tile.stomp_tip")))
        tiles.addWidget(_tile(_pct(fc.stomp(False)), tr("pred.tile.stomp_loss"), c.loss, tr("pred.tile.stomp_tip")))
        tiles.addWidget(_tile(_pct(fc.p_round_neutral, 1), tr("pred.tile.round"), tip=tr("pred.tile.round_tip")))
        lay.addLayout(tiles)
        return card

    def _scores_card(self, fc: Forecast) -> QFrame:
        card, lay, _ = make_card(tr("pred.scores"))
        chart = HBarList()
        rows = []
        for (a, b), p in fc.top_scores(8):
            rows.append((f"{a}:{b}", p, _pct(p, 1), c.ally if a > b else c.enemy))
        chart.set_rows(rows)
        lay.addWidget(chart)
        hint = QLabel(tr("pred.scores_hint", ot=_pct(fc.p_overtime)))
        hint.setWordWrap(True)
        hint.setFont(font(8, False))
        hint.setStyleSheet(f"color: {c.muted};")
        lay.addWidget(hint)
        lay.addStretch()
        return card

    def _reasons_card(self, fc: Forecast) -> QFrame:
        card, lay, _ = make_card(tr("pred.why"))
        head = QLabel(tr("pred.why_head", p=_pct(fc.win_prob)))
        head.setFont(font(10, True))
        head.setWordWrap(True)
        lay.addWidget(head)
        shown = 0
        for r in fc.reasons:
            text, impact = reason_text(r)
            row = QHBoxLayout()
            row.setSpacing(8)
            chip = QLabel(f"{impact * 100:+.1f}" if impact is not None else "•")
            chip.setFixedWidth(46)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFont(font(9, True))
            if impact is None:
                chip.setStyleSheet(f"color: {c.muted};")
            else:
                col = c.win if impact > 0 else c.loss
                chip.setStyleSheet(f"color: {col}; background-color: {rgba(col, 0.14)}; border-radius: 8px; padding: 1px 0;")
                chip.setToolTip(tr("pred.chip_tip"))
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setFont(font(9, False))
            lbl.setStyleSheet(f"color: {c.text if impact is not None else c.muted};")
            row.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(lbl, 1)
            lay.addLayout(row)
            shown += 1
        lay.addStretch()
        return card

    # ── команды ──
    def _team_card(self, fc: Forecast, lobby, ally: bool) -> QFrame:
        card = QFrame()
        card.setObjectName("TeamCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        banner = QFrame()
        banner.setObjectName("AllyHeader" if ally else "EnemyHeader")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(10, 6, 10, 6)
        t = QLabel(tr("live.your_team" if ally else "live.enemy_team"))
        t.setFont(font(10, True))
        bl.addWidget(t)
        bl.addStretch()
        pfs = fc.ally if ally else fc.enemy
        mean = (fc.ally_rating if ally else fc.enemy_rating)
        comp = fc.ally_comp if ally else fc.enemy_comp
        sub = QLabel(tr("pred.team_rating", v=f"{mean:.0f}"))
        sub.setFont(font(9, False))
        sub.setStyleSheet(f"color: {c.muted};")
        sub.setToolTip(tr("pred.team_rating_tip"))
        bl.addWidget(sub)
        if comp[1]:
            warn = QLabel("⚠ " + ", ".join(tr(x) for x in comp[1]))
            warn.setFont(font(9, True))
            warn.setStyleSheet(f"color: {c.warn};")
            bl.addSpacing(14)
            bl.addWidget(warn)
        lay.addWidget(banner)

        table = QTableWidget(len(pfs), len(COLS))
        table.setHorizontalHeaderLabels([tr(f"pred.col.{k}") for k in COLS])
        h = table.horizontalHeader()
        for col in range(len(COLS)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 1 else QHeaderView.ResizeMode.Fixed)
        for col, w in COL_W.items():
            table.setColumnWidth(col, w)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setFixedHeight((h.height() or 26) + len(pfs) * ROW_H + 4)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        best_acs = max((pf.exp_acs for pf in pfs), default=0)
        for row, pf in enumerate(sorted(pfs, key=lambda x: -x.exp_acs)):
            table.setRowHeight(row, ROW_H)
            p = pf.m.p
            bar = c.ally if ally else c.enemy
            table.setCellWidget(row, 0, create_cell(p.agent, self._icons.agent(p.agent_uuid), bar_color=bar))
            name_color = c.title if p.is_me else (c.muted if p.hidden else c.text)
            table.setCellWidget(row, 1, create_cell(p.name + (" ◄" if p.is_me else ""), text_color=name_color, is_bold=p.is_me))
            table.setCellWidget(row, 2, create_cell(f"{pf.exp_acs:.0f}", is_bold=True,
                                                    text_color=c.win if pf.exp_acs == best_acs else c.text))
            table.setCellWidget(row, 3, create_cell(f"{pf.exp_kills:.0f} / {pf.exp_deaths:.0f}"))
            top = pf.p_mvp_lobby >= 0.25
            table.setCellWidget(row, 4, create_cell(_pct(pf.p_mvp_lobby), is_bold=top, text_color=c.title if top else c.muted))
            table.setCellWidget(row, 5, create_cell(_pct(pf.p_best_team), text_color=c.win if pf.p_best_team >= 0.35 else c.muted))
            table.setCellWidget(row, 6, create_cell(_pct(pf.p_worst_team), text_color=c.loss if pf.p_worst_team >= 0.35 else c.muted))
        lay.addWidget(table)
        return card
