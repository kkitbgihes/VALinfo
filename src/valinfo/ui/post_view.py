"""Экран после матча: сводка, матрица дуэлей, экономика, хронология раундов."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from valinfo.i18n import tr
from valinfo.modes import mode_for
from valinfo.models import CountryResult
from valinfo.settings import get_settings
from valinfo.ui.theme import c
from valinfo.ui.country_cell import build_country_cell
from valinfo.ui.icons import IconProvider
from valinfo.ui.widgets import NickLabel, create_cell, create_nick_cell, font

SUMMARY_KEYS = ["player", "country", "agent", "acs", "kda", "kd", "adr", "hs", "hits", "fkfd", "spike", "multi",
                "reward"]
ECO_KEYS = ["player", "agent", "spent", "avg", "ability_c", "ability_q", "ability_e", "ability_x"]
ROUNDS_KEYS = ["round", "winner", "cond", "site", "planter", "defuser"]
SUMMARY_WIDTHS = {0: 190, 1: 62, 2: 118, 3: 50, 4: 92, 5: 48, 6: 48, 7: 52, 8: 118, 9: 66, 10: 78, 11: 86}
COL_COUNTRY = 1


def _nick(p) -> str:
    return f"{p['name'][0]}#{p['name'][1]}"


class PostMatchView(QWidget):
    back_requested = pyqtSignal()

    def __init__(self, icons: IconProvider, parent=None):
        super().__init__(parent)
        self._icons = icons
        self._data = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        top_bar = QHBoxLayout()
        self.btn_back = btn_back = QPushButton(tr("post.back"))
        btn_back.clicked.connect(self.back_requested)

        self.lbl_title = QLabel(tr("post.analytics"))
        self.lbl_title.setFont(font(14, True))
        self.lbl_title.setStyleSheet(f"color: {c.title};")

        top_bar.addWidget(btn_back)
        top_bar.addStretch()
        top_bar.addWidget(self.lbl_title)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.tabs = QTabWidget()
        self._build_summary_tab()
        self._build_duels_tab()
        self._build_eco_tab()
        self._build_rounds_tab()
        layout.addWidget(self.tabs)

    # ═══════════ построение вкладок ═══════════
    @staticmethod
    def _prepare_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def _build_summary_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.table_summary = QTableWidget()
        self.table_summary.setColumnCount(len(SUMMARY_KEYS))
        self.table_summary.setHorizontalHeaderLabels([tr(f"post.sum.{k}") for k in SUMMARY_KEYS])
        self._prepare_table(self.table_summary)
        hdr = self.table_summary.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col, w in SUMMARY_WIDTHS.items():
            self.table_summary.setColumnWidth(col, w)
        lay.addWidget(self.table_summary)
        self.tabs.addTab(tab, tr("post.tab.summary"))

    def _build_duels_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._legend)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("DuelsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self._duels_host = QWidget()
        self._duels_host.setObjectName("DuelsGridHost")
        self._duels_grid = QGridLayout(self._duels_host)
        self._duels_grid.setContentsMargins(0, 0, 0, 0)
        self._duels_grid.setSpacing(6)
        body_layout.addWidget(self._duels_host)
        body_layout.addStretch()
        scroll.setWidget(body)
        lay.addWidget(scroll)
        self.tabs.addTab(tab, tr("post.tab.duels"))

    def _build_eco_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.table_eco = QTableWidget()
        self.table_eco.setColumnCount(8)
        self.table_eco.setHorizontalHeaderLabels([tr(f"post.eco.{k}") for k in ECO_KEYS])
        self._prepare_table(self.table_eco)
        self.table_eco.setColumnWidth(0, 190)
        self.table_eco.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table_eco)
        self.tabs.addTab(tab, tr("post.tab.eco"))

    def _build_rounds_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.table_rounds = QTableWidget()
        self.table_rounds.setColumnCount(6)
        self.table_rounds.setHorizontalHeaderLabels([tr(f"post.rounds.{k}") for k in ROUNDS_KEYS])
        self._prepare_table(self.table_rounds)
        self.table_rounds.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table_rounds.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table_rounds)
        self.tabs.addTab(tab, tr("post.tab.rounds"))

    # ═══════════ дуэли ═══════════
    def _duel_header(self, p, enemy, is_me):
        accent = c.enemy if enemy else c.ally
        edge = "border-bottom" if enemy else "border-left"
        f = QFrame()
        f.setObjectName("DuelHead")
        f.setStyleSheet(
            f"QFrame#DuelHead {{ background-color: {c.header}; border: none;"
            f" {edge}: 3px solid {accent}; border-radius: 4px; }}")
        f.setToolTip(f"{_nick(p)} — {p['agent_name']}")

        icon = QLabel()
        pix = self._icons.agent(p["agent_uuid"], 40)
        if pix:
            icon.setPixmap(pix)
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        nick = p["name"][0] or tr("col.player")
        if len(nick) > 14:
            nick = nick[:13] + "…"
        # кликабельный ник: на экране — укороченный, в буфер — полный nickname#tag
        lbl_name = NickLabel(nick + (" ◄" if is_me else ""), _nick(p), c.title if is_me else c.text, True)
        lbl_agent = QLabel(p["agent_name"])
        lbl_agent.setFont(font(8))
        lbl_agent.setStyleSheet(f"color: {c.muted};")

        if enemy:
            lay = QVBoxLayout(f)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(2)
            lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(lbl_name, 0, Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(lbl_agent, 0, Qt.AlignmentFlag.AlignHCenter)
        else:
            lay = QHBoxLayout(f)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(8)
            lay.addWidget(icon)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addStretch()
            col.addWidget(lbl_name, 0, Qt.AlignmentFlag.AlignLeft)
            col.addWidget(lbl_agent)
            col.addStretch()
            lay.addLayout(col)
            lay.addStretch()
        return f

    @staticmethod
    def _duel_cell(killed, died):
        if killed > died:
            bg = "rgba(56,193,114,0.13)"
        elif died > killed:
            bg = "rgba(224,85,97,0.13)"
        else:
            bg = c.header
        f = QFrame()
        f.setObjectName("DuelCell")
        f.setStyleSheet(
            f"QFrame#DuelCell {{ background-color: {bg}; border: 1px solid {c.border}; border-radius: 6px; }}")
        f.setMinimumHeight(64)
        f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QHBoxLayout(f)
        lay.setContentsMargins(4, 4, 4, 4)
        lbl = QLabel(
            f'<span style="color:{c.win if killed else c.muted};">{killed}</span>'
            f'<span style="color:{c.muted};"> / </span>'
            f'<span style="color:{c.loss if died else c.muted};">{died}</span>')
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setFont(font(16, True))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)
        return f

    def _build_duels_matrix(self, data):
        grid = self._duels_grid
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for col in range(12):
            grid.setColumnStretch(col, 0)

        players = data["players"]
        if not players:
            return
        me = data.get("me")
        my_team = next((p["team"] for p in players if p["puuid"] == me), players[0]["team"])
        allies = [p for p in players if p["team"] == my_team]
        enemies = [p for p in players if p["team"] != my_team]
        duels = data.get("duels") or {}

        corner = QLabel(tr("post.corner"))
        corner.setFont(font(8, True))
        corner.setStyleSheet(f"color: {c.muted};")
        corner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(corner, 0, 0)
        grid.setColumnMinimumWidth(0, 190)

        for j, e in enumerate(enemies):
            grid.addWidget(self._duel_header(e, True, False), 0, j + 1)
            grid.setColumnStretch(j + 1, 1)

        for i, a in enumerate(allies):
            grid.addWidget(self._duel_header(a, False, a["puuid"] == me), i + 1, 0)
            for j, e in enumerate(enemies):
                killed = duels.get(a["puuid"], {}).get(e["puuid"], 0)
                died = duels.get(e["puuid"], {}).get(a["puuid"], 0)
                grid.addWidget(self._duel_cell(killed, died), i + 1, j + 1)

    # ═══════════ данные ═══════════
    def retranslate(self) -> None:
        """Язык/тема сменились: тексты шапок и содержимое перерисовываем из сохранённых данных."""
        self.btn_back.setText(tr("post.back"))
        self.lbl_title.setStyleSheet(f"color: {c.title};")
        self.tabs.setTabText(0, tr("post.tab.summary"))
        self.tabs.setTabText(1, tr("post.tab.duels"))
        self.tabs.setTabText(2, tr("post.tab.eco"))
        self.tabs.setTabText(3, tr("post.tab.rounds"))
        self.table_summary.setHorizontalHeaderLabels([tr(f"post.sum.{k}") for k in SUMMARY_KEYS])
        self.table_eco.setHorizontalHeaderLabels([tr(f"post.eco.{k}") for k in ECO_KEYS])
        self.table_rounds.setHorizontalHeaderLabels([tr(f"post.rounds.{k}") for k in ROUNDS_KEYS])
        self._legend.setText(
            f'<span style="color:{c.win};"><b>{tr("post.legend.green")}</b></span> — {tr("post.legend.green_text")}'
            f' &nbsp;·&nbsp; <span style="color:{c.loss};"><b>{tr("post.legend.red")}</b></span> — {tr("post.legend.red_text")}')
        self._legend.setStyleSheet(f"color: {c.muted};")
        if self._data is not None:
            self.show_data(self._data)

    def show_data(self, data: dict) -> None:
        self._data = data
        self.table_summary.setColumnHidden(COL_COUNTRY, not get_settings().country_detection)
        self._legend.setText(
            f'<span style="color:{c.win};"><b>{tr("post.legend.green")}</b></span> — {tr("post.legend.green_text")}'
            f' &nbsp;·&nbsp; <span style="color:{c.loss};"><b>{tr("post.legend.red")}</b></span> — {tr("post.legend.red_text")}')
        self._legend.setStyleSheet(f"color: {c.muted};")
        mode = tr(f"mode.{mode_for(data.get('mode')).key}")
        self.lbl_title.setText(tr("post.title", map=data["map_name"].upper(), mode=mode.upper()))
        players = data["players"]
        me_team = next((p["team"] for p in players if p["puuid"] == data["me"]), None)
        icons = self._icons
        countries = data.get("countries") or {}

        self.setUpdatesEnabled(False)
        try:
            # ── сводка ──
            t = self.table_summary
            t.setRowCount(len(players))
            for row, p in enumerate(players):
                t.setRowHeight(row, 36)
                is_me = (p["puuid"] == data["me"])
                name_str = _nick(p) + (" ◄" if is_me else "")

                t.setCellWidget(row, 0, create_nick_cell(
                    name_str, _nick(p), c.title if is_me else c.text, is_me))
                t.setCellWidget(row, COL_COUNTRY, build_country_cell(
                    CountryResult.from_dict(countries.get(p["puuid"])), icons))
                t.setCellWidget(row, 2, create_cell(p["agent_name"], icons.agent(p["agent_uuid"])))
                t.setCellWidget(row, 3, create_cell(p["acs"], is_bold=True))
                t.setCellWidget(row, 4, create_cell(f"{p['kills']} / {p['deaths']} / {p['assists']}"))
                kd_val = round(p['kills'] / max(p['deaths'], 1), 2)
                t.setCellWidget(row, 5, create_cell(kd_val, text_color=c.win if kd_val >= 1.0 else c.loss))
                t.setCellWidget(row, 6, create_cell(p["adr"]))
                t.setCellWidget(row, 7, create_cell(f"{p['hs_percent']}%"))
                t.setCellWidget(row, 8, create_cell(f"{p['hs']}H / {p['bs']}B / {p['ls']}L", text_color=c.muted))
                t.setCellWidget(row, 9, create_cell(f"{p['fk']} / {p['fd']}"))
                t.setCellWidget(row, 10, create_cell(f"{p['plants']} / {p['defuses']}"))

                mk = []
                if p["3k"]: mk.append(f"3K:{p['3k']}")
                if p["4k"]: mk.append(f"4K:{p['4k']}")
                if p["5k"]: mk.append(f"ACE:{p['5k']}")
                t.setCellWidget(row, 11, create_cell(" ".join(mk) if mk else "—"))

                mvp_txt = p.get("mvp", "—")
                t.setCellWidget(row, 12, create_cell(
                    mvp_txt, text_color=c.title if mvp_txt != "—" else c.muted, is_bold=True))

            # ── дуэли ──
            self._build_duels_matrix(data)

            # ── экономика ──
            e = self.table_eco
            e.setRowCount(len(players))
            for row, p in enumerate(players):
                e.setCellWidget(row, 0, create_nick_cell(_nick(p), _nick(p)))
                e.setCellWidget(row, 1, create_cell(p["agent_name"], icons.agent(p["agent_uuid"])))
                e.setCellWidget(row, 2, create_cell(f"{p['spent_credits']} ¤"))
                e.setCellWidget(row, 3, create_cell(f"{p['avg_spent']} ¤"))
                e.setCellWidget(row, 4, create_cell(p["ability_casts"]["c"]))
                e.setCellWidget(row, 5, create_cell(p["ability_casts"]["q"]))
                e.setCellWidget(row, 6, create_cell(p["ability_casts"]["e"]))
                e.setCellWidget(row, 7, create_cell(p["ability_casts"]["x"], is_bold=True, text_color=c.title))

            # ── раунды: простой текст → обычные item'ы вместо ~300 виджетов ──
            rounds = data["timeline"]
            r = self.table_rounds
            r.setRowCount(len(rounds))
            for row, rd in enumerate(rounds):
                mine = rd["winning_team"] == me_team
                win_color = c.ally if mine else c.enemy
                values = [
                    (tr("post.round_n", n=rd["round_num"]), c.text, True),
                    (tr("post.winner_you") if mine else tr("post.winner_enemy"), win_color, False),
                    (rd["win_result"], c.text, False),
                    (rd["plant_site"] or "—", c.text, False),
                    (rd["planter"], c.text, False),
                    (rd["defuser"], c.text, False),
                ]
                for col, (text, color, bold) in enumerate(values):
                    item = QTableWidgetItem(str(text))
                    item.setFont(font(9, bold))
                    item.setForeground(QBrush(QColor(color)))
                    r.setItem(row, col, item)
        finally:
            self.setUpdatesEnabled(True)
