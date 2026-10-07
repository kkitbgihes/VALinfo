"""Live-таблица матча: две команды (5x5) или одна общая таблица (Deathmatch и прочие FFA-режимы).

Страница не знает про шапку и вкладки — ими занимается MainWindow. Здесь только карточки с таблицами.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QVBoxLayout, QWidget

from valinfo.config import COUNTRY_ON_PREGAME
from valinfo.constants import RANKS, THRESHOLDS
from valinfo.i18n import tr
from valinfo.models import CountryResult, CountryStatus
from valinfo.modes import FFA, detect_layout, mode_for
from valinfo.settings import get_settings
from valinfo.ui.icons import IconProvider
from valinfo.ui.country_cell import build_country_cell
from valinfo.ui.theme import c
from valinfo.ui.widgets import create_cell, create_nick_cell, font

ROW_H = 34
_LOADING = CountryResult(CountryStatus.LOADING)
SPIN_FRAMES = ["●○○", "○●○", "○○●", "○●○"]

COLS = ["agent", "country", "player", "rank", "peak", "kd", "hs", "adr", "acs", "wr", "streak", "main", "lvl"]
COL_W = {0: 112, 1: 56, 3: 150, 4: 168, 5: 52, 6: 52, 7: 52, 8: 52, 9: 58, 10: 58, 11: 96, 12: 44}


def rank_label(tier: int):
    """(название, цвет) ранга по tier-id."""
    if tier >= 3:
        idx = (tier - 3) // 3
        return f"{RANKS[idx]} {(tier - 3) % 3 + 1}", c.ranks[min(idx, 8)]
    return tr("rank.unranked"), c.muted


class LiveView(QWidget):
    def __init__(self, icons: IconProvider, parent=None):
        super().__init__(parent)
        self._icons = icons
        self._spin_i = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(220)
        self._spin_timer.timeout.connect(self._tick_spinner)  # крутится только пока есть «pending»-бейдж
        self._last: Optional[dict] = None
        self._layout_kind = "teams"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        self.card_ally, self.table_ally = self._create_card("AllyHeader")
        self.card_enemy, self.table_enemy = self._create_card("EnemyHeader")
        layout.addWidget(self.card_ally)
        layout.addWidget(self.card_enemy)
        layout.addStretch()
        self._set_titles("teams")

    # ── построение ──
    def _create_card(self, header_object_name):
        card = QFrame()
        card.setObjectName("TeamCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        banner = QFrame()
        banner.setObjectName(header_object_name)
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        card.banner = banner
        card.title_lbl = QLabel()
        card.title_lbl.setFont(font(10, True))
        banner_layout.addWidget(card.title_lbl)
        banner_layout.addStretch()

        badge = QLabel("")
        badge.setFont(font(9, True))
        badge.hide()
        banner_layout.addWidget(badge)
        card.status_lbl = badge
        card.party_state = None
        card.party_badge_key = None
        layout.addWidget(banner)

        table = QTableWidget()
        table.setColumnCount(len(COLS))
        h = table.horizontalHeader()
        for col in range(len(COLS)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 2 else QHeaderView.ResizeMode.Fixed)
        for col, w in COL_W.items():
            table.setColumnWidth(col, w)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table._row_sigs = []                     # подписи отрисованных строк — для diff'а
        layout.addWidget(table)
        return card, table

    def _set_titles(self, kind: str) -> None:
        self._layout_kind = kind
        labels = [tr(f"col.{k}") for k in COLS]
        for t in (self.table_ally, self.table_enemy):
            t.setHorizontalHeaderLabels(labels)
        if kind == FFA:
            self.card_ally.title_lbl.setText(tr("live.players"))
            self.card_ally.banner.setObjectName("NeutralHeader")
            self.card_enemy.hide()
        else:
            self.card_ally.title_lbl.setText(tr("live.your_team"))
            self.card_ally.banner.setObjectName("AllyHeader")
            self.card_enemy.title_lbl.setText(tr("live.enemy_team"))
            self.card_enemy.show()
        # смена objectName требует «переполировки» стиля
        for card in (self.card_ally, self.card_enemy):
            card.banner.style().unpolish(card.banner)
            card.banner.style().polish(card.banner)
        self.apply_country_visibility()

    def apply_country_visibility(self) -> None:
        on = bool(get_settings().country_detection)
        for t in (self.table_ally, self.table_enemy):
            t.setColumnHidden(1, not on)

    # ── публичный API ──
    def clear(self) -> None:
        """Убрать всё содержимое (матч закончился / ещё не начался)."""
        self._last = None
        for t in (self.table_ally, self.table_enemy):
            t.setRowCount(0)
            t._row_sigs.clear()
            t.setFixedHeight((t.horizontalHeader().height() or 26))
        for card in (self.card_ally, self.card_enemy):
            card.party_state, card.party_badge_key = None, None
            card.status_lbl.hide()
        self._spin_timer.stop()

    def retheme(self) -> None:
        """Тема/язык сменились: перерисовать всё с нуля из последних данных."""
        for t in (self.table_ally, self.table_enemy):
            t._row_sigs.clear()
        for card in (self.card_ally, self.card_enemy):
            card.party_badge_key = None
        self._set_titles(self._layout_kind)
        if self._last is not None:
            self.update_data(self._last)

    def update_data(self, data: dict) -> None:
        self._last = data
        players = data["players"]
        mode = mode_for(data.get("queue"))
        kind = data.get("layout") or detect_layout(mode, players)
        if kind != self._layout_kind:
            for t in (self.table_ally, self.table_enemy):
                t._row_sigs.clear()
            self._set_titles(kind)
        parties = data.get("parties") or {}
        by_party = lambda team: sorted(team, key=lambda p: (p["subject"] not in parties, parties.get(p["subject"], 0)))

        if kind == FFA:
            mine = [p for p in players if p["subject"] == data["me"]]
            rest = [p for p in players if p["subject"] != data["me"]]
            self._populate(self.table_ally, mine + rest, data)
            self._populate(self.table_enemy, [], data)
            for card in (self.card_ally, self.card_enemy):
                self._apply_party_badge(card, None, 0)
            return

        my_team = next((p["team"] for p in players if p["subject"] == data["me"]), players[0]["team"])
        ally = [p for p in players if p["team"] == my_team]
        enemy = [p for p in players if p["team"] != my_team]
        self._populate(self.table_ally, by_party(ally), data)
        self._populate(self.table_enemy, by_party(enemy), data)

        status = data.get("party_status") or {}
        groups_in = lambda team: len({parties[p["subject"]] for p in team if p["subject"] in parties})
        on = bool(get_settings().party_detection)
        self._apply_party_badge(self.card_ally, status.get(my_team) if (ally and on) else None, groups_in(ally))
        self._apply_party_badge(self.card_enemy, status.get(enemy[0]["team"]) if (enemy and on) else None, groups_in(enemy))

    # ── пати-бейдж ──
    def _tick_spinner(self):
        self._spin_i = (self._spin_i + 1) % len(SPIN_FRAMES)
        for card in (self.card_ally, self.card_enemy):
            if card.party_state == "pending":
                card.status_lbl.setText(f"{SPIN_FRAMES[self._spin_i]}  {tr('live.party_detecting')}")

    def _apply_party_badge(self, card, state, n_groups=0):
        key = (state, n_groups if state == "done" else 0)
        if card.party_badge_key == key:
            return
        card.party_badge_key = key
        card.party_state = state
        lbl = card.status_lbl
        if state == "pending":
            lbl.setStyleSheet(
                f"color: {c.warn}; background-color: rgba(255,184,77,0.14);"
                f"border: 1px solid {c.warn}; border-radius: 9px; padding: 2px 10px;")
            lbl.setText(f"{SPIN_FRAMES[self._spin_i]}  {tr('live.party_detecting')}")
            lbl.setToolTip(tr("live.party_tip"))
            lbl.show()
        else:
            lbl.hide()
        pending = any(cd.party_state == "pending" for cd in (self.card_ally, self.card_enemy))
        if pending and not self._spin_timer.isActive():
            self._spin_timer.start()
        elif not pending:
            self._spin_timer.stop()

    # ── страна ──
    @staticmethod
    def _country_result_for(p, is_me, data) -> Optional[CountryResult]:
        """None = для этой строки страну не ищем (пустая ячейка)."""
        hidden = bool((p.get("ident") or {}).get("Incognito"))
        if (not get_settings().country_detection or is_me or hidden
                or (data.get("kind") != "ingame" and not COUNTRY_ON_PREGAME)):
            return None
        return (data.get("countries") or {}).get(p["subject"]) or _LOADING

    # ── таблица ──
    def _row_signature(self, p, data, party_bar, country, show_date):
        s = p["subject"]
        st = data["info"].get(s)
        info_done = data.get("info_done")
        return (
            s, p["agent"], party_bar, data["names"].get(s), s == data["me"],
            tuple(sorted((p.get("ident") or {}).items())),
            (s not in info_done) if info_done is not None else False,
            tuple(sorted(st.items())) if st else None,
            country, show_date,
        )

    def _populate(self, table: QTableWidget, players, data) -> None:
        count = len(players)
        sigs = table._row_sigs
        settings = get_settings()
        party_on, show_date = bool(settings.party_detection), bool(settings.show_peak_date)
        colors = c.party
        table.setUpdatesEnabled(False)
        try:
            if table.rowCount() != count:
                table.setRowCount(count)
                table.setFixedHeight((table.horizontalHeader().height() or 26) + count * ROW_H + 2)
                del sigs[count:]
            sigs.extend([None] * (count - len(sigs)))

            for row, p in enumerate(players):
                s = p["subject"]
                is_me = (s == data["me"])
                party_idx = (data.get("parties") or {}).get(s)
                if not party_on:
                    bar = None
                elif party_idx is None:
                    bar = ""
                else:
                    bar = colors[party_idx % len(colors)]
                country = self._country_result_for(p, is_me, data)

                sig = self._row_signature(p, data, bar, country, show_date)
                if sigs[row] == sig:
                    continue                      # ничего не изменилось — виджеты строки не трогаем
                sigs[row] = sig
                table.setRowHeight(row, ROW_H)
                self._build_row(table, row, p, data, bar, country, is_me, show_date)
        finally:
            table.setUpdatesEnabled(True)

    def _build_row(self, table, row, p, data, bar, country, is_me, show_date) -> None:
        icons = self._icons
        s = p["subject"]
        ident = p["ident"]
        agent_uuid = p["agent"].lower()
        agent_name = data["agents"].get(agent_uuid, "—")
        nm = data["names"].get(s)
        st = data["info"].get(s, {})

        agent_pix = icons.agent(agent_uuid) if agent_uuid else None
        table.setCellWidget(row, 0, create_cell(agent_name if agent_uuid else "—", agent_pix, bar_color=bar))
        table.setCellWidget(row, 1, build_country_cell(country, icons))

        copy_text = None
        if ident.get("Incognito") and not is_me:
            name_text, name_color = f"{agent_name} ({tr('common.hidden')})", c.muted
        elif nm:
            name_text = f"{nm[0]}#{nm[1]}"
            copy_text = name_text                 # в буфер обмена попадёт nickname#tag
            name_color = c.title if is_me else c.text
        else:
            name_text, name_color = tr("common.loading"), c.muted
        if is_me:
            name_text += " ◄"
        table.setCellWidget(row, 2, create_nick_cell(name_text, copy_text, name_color, is_me))

        info_done = data.get("info_done")
        loading = (s not in info_done) if info_done is not None else False

        if s not in data["info"]:
            table.setCellWidget(row, 3, create_cell("…", text_color=c.muted))
            table.setCellWidget(row, 4, create_cell("…", text_color=c.muted))
        else:
            cur_tier = st.get("cur_tier") or 0
            rr = st.get("rr") or 0
            r_name, r_color = rank_label(cur_tier)
            table.setCellWidget(row, 3, create_cell(
                f"{r_name} ({rr}RR)" if cur_tier >= 3 else r_name, icons.rank(cur_tier), text_color=r_color))

            peak_tier = st.get("peak_tier") or 0
            p_name, p_color = rank_label(peak_tier)
            date = st.get("peak_date") if (show_date and peak_tier >= 3) else None
            table.setCellWidget(row, 4, create_cell(
                p_name, icons.rank(peak_tier), text_color=p_color, sub_text=date, sub_tip=tr("live.peak_date_tip")))

        metrics = [
            (5, st.get("kd"), "kd", "{:.2f}"),
            (6, st.get("hs"), "hs", "{:.1f}%"),
            (7, st.get("adr"), "adr", "{:.0f}"),
            (8, st.get("acs"), "acs", "{:.0f}"),
            (9, st.get("winrate"), "wr", "{:.0f}%"),
        ]
        for col, val, key, fmt in metrics:
            txt = fmt.format(val) if val is not None else ("…" if loading else "—")
            if val is None:
                clr = c.muted
            elif val >= THRESHOLDS[key]["good"]:
                clr = c.win
            elif val < THRESHOLDS[key]["bad"]:
                clr = c.loss
            else:
                clr = c.mid
            table.setCellWidget(row, col, create_cell(txt, text_color=clr, is_bold=True))

        strk = st.get("streak") or ("…" if loading else "—")
        strk_clr = c.win if "+" in strk else (c.loss if "-" in strk else c.muted)
        table.setCellWidget(row, 10, create_cell(strk, text_color=strk_clr, is_bold=True))

        main_uuid = st.get("main_agent_uuid")
        table.setCellWidget(row, 11, create_cell(
            st.get("main_agent") or ("…" if loading else "—"), icons.agent(main_uuid) if main_uuid else None))

        lvl = "—" if (ident.get("HideAccountLevel") and not is_me) else str(ident.get("AccountLevel", "—"))
        table.setCellWidget(row, 12, create_cell(lvl, text_color=c.muted))
