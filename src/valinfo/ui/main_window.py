"""Главное окно: заголовок → шапка со статусом → вкладки → страницы. Все данные приходят из WorkerThread.

Вкладки — это данные (`TABS`): чтобы добавить новую, достаточно написать виджет с `set_active()` / `retheme()`
и добавить одну запись в `_build_tabs()`.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QLocale, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from valinfo.config import UI_COALESCE_MS
from valinfo.i18n import translator, tr
from valinfo.modes import mode_for
from valinfo.settings import Settings, get_settings
from valinfo.storage.assets import AssetStore
from valinfo.ui import theme
from valinfo.ui.analysis_service import AnalysisService
from valinfo.ui.charts import NavBar
from valinfo.ui.country_cell import build_legend
from valinfo.ui.icons import IconProvider
from valinfo.ui.live_tab import LiveTab
from valinfo.ui.players_view import PlayersView
from valinfo.ui.predictions_view import PredictionsView
from valinfo.ui.settings_view import SettingsView
from valinfo.ui.widgets import CustomTitleBar, font
from valinfo.worker import WorkerThread

TAB_KEYS = ("live", "prediction", "players", "settings")


def apply_language(settings: Settings) -> None:
    translator.set_language(translator.resolve(settings.language, QLocale.system().name()))


class MainWindow(QMainWindow):
    def __init__(self, assets: AssetStore, start_worker: bool = True, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or get_settings()
        apply_language(self.settings)
        theme.apply_theme(self.settings.theme)

        self._flags = Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(self._flags | (Qt.WindowType.WindowStaysOnTopHint if self.settings.always_on_top else Qt.WindowType(0)))
        self.setWindowTitle("ValInfo - by khs")
        self.resize(1200, 700)
        self.setMinimumSize(1180, 640)
        self.setStyleSheet(theme.build_qss())

        self._icons = IconProvider(assets)
        self._payload: Optional[dict] = None          # последнее live-состояние (None — матча нет)
        self._status: tuple = ("status.init", {})
        self._current = "live"

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.title_bar = CustomTitleBar(self)
        root.addWidget(self.title_bar)
        root.addWidget(self._build_header())

        self.nav = NavBar()
        for key in TAB_KEYS:
            self.nav.add_tab(key, "")
        self.nav.finish()
        self.nav.on_select(self.switch_tab)
        nav_wrap = QWidget()
        nl = QHBoxLayout(nav_wrap)
        nl.setContentsMargins(12, 8, 12, 0)
        nl.addWidget(self.nav)
        root.addWidget(nav_wrap)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self.service = AnalysisService(self)
        self.tabs: dict = {}
        self._build_tabs()

        # Частые live-обновления (каждый игрок шлёт по несколько частичных результатов + страны + пати)
        # склеиваем: рисуем самое свежее состояние не чаще раза в UI_COALESCE_MS.
        self._pending_live: Optional[dict] = None
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._flush_live)

        self.settings.subscribe(self._on_setting)
        self._retranslate()
        self.nav.select("live", notify=False)
        self.tabs["live"].set_active(True)

        self.worker = WorkerThread(assets)
        self.worker.live_data_ready.connect(self._on_live)
        self.worker.post_match_data_ready.connect(self._on_post_match)
        self.worker.status_changed.connect(self._on_status)
        self.worker.no_match.connect(self._on_no_match)
        if start_worker:
            QTimer.singleShot(0, self.worker.start)   # окно показывается сразу, воркер стартует следом

    # ── построение ──
    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("HeaderBar")
        self._header_lay = QHBoxLayout(header)
        self._header_lay.setContentsMargins(12, 8, 12, 8)
        self.lbl_title = QLabel()
        self.lbl_title.setFont(font(11, True))
        self.lbl_status = QLabel()
        self.lbl_status.setFont(font(10, True))
        self._legend_host = QHBoxLayout()
        self._legend_host.setContentsMargins(0, 0, 0, 0)
        self._header_lay.addWidget(self.lbl_title)
        self._header_lay.addStretch()
        self._header_lay.addLayout(self._legend_host)
        self._header_lay.addSpacing(18)
        self._header_lay.addWidget(self.lbl_status)
        header.setContentsMargins(0, 0, 0, 0)
        wrap = QFrame()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(12, 12, 12, 0)
        wl.addWidget(header)
        return wrap

    def _build_tabs(self) -> None:
        self.tabs = {
            "live": LiveTab(self._icons),
            "prediction": PredictionsView(self.service, self._icons),
            "players": PlayersView(self.service, self._icons),
            "settings": SettingsView(self.settings, translator),
        }
        for key in TAB_KEYS:
            self.pages.addWidget(self.tabs[key])
        self.live_tab: LiveTab = self.tabs["live"]
        self.live_view = self.live_tab.live_view
        self.post_view = self.live_tab.post_view
        self._analysis_tabs = (self.tabs["prediction"], self.tabs["players"])

    # ── вкладки ──
    def switch_tab(self, key: str) -> None:
        if key == self._current:
            return
        prev = self.tabs[self._current]
        if hasattr(prev, "set_active"):
            prev.set_active(False)
        self._current = key
        self.pages.setCurrentWidget(self.tabs[key])
        self.nav.select(key, notify=False)
        cur = self.tabs[key]
        if hasattr(cur, "set_active"):
            cur.set_active(True)               # только теперь вкладка начинает считать/рисовать
        self._update_legend()

    # ── данные воркера ──
    def _on_live(self, data: dict) -> None:
        self._pending_live = data
        if not self._live_timer.isActive():
            self._live_timer.start(UI_COALESCE_MS)

    def _flush_live(self) -> None:
        data, self._pending_live = self._pending_live, None
        if data is None:
            return
        self._payload = data
        self.live_tab.set_live(data)
        for t in self._analysis_tabs:
            t.set_payload(data)

    def _on_post_match(self, data: dict) -> None:
        self._pending_live = None                 # отложенный live-кадр уже неактуален
        self._live_timer.stop()
        self._clear_payload()
        self.live_tab.set_post(data)

    def _on_no_match(self) -> None:
        self._pending_live = None
        self._live_timer.stop()
        if self._payload is None and self.live_tab.state != "live":
            return                                # уже пусто — ничего не делаем (сигнал приходит каждый тик)
        self._clear_payload()
        self.live_tab.on_no_match()

    def _clear_payload(self) -> None:
        self._payload = None
        self.service.invalidate()
        for t in self._analysis_tabs:
            t.set_payload(None)

    # ── статус ──
    def _on_status(self, key: str, params: dict) -> None:
        self._status = (key, params)
        self._render_status()

    def _render_status(self) -> None:
        key, p = self._status
        if key == "status.match":
            q = mode_for(p.get("mode"))
            text = tr("status.match", map=p["map"], state=tr(f"state.{p['state']}"), mode=tr(f"mode.{q.key}"))
            if p.get("loading"):
                text += tr("status.loading_data")
        else:
            text = tr(key, **p)
        self.lbl_status.setText(text)

    def _update_legend(self) -> None:
        while self._legend_host.count():
            w = self._legend_host.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if self.settings.country_detection and self._current == "live":
            self._legend_host.addWidget(build_legend())

    # ── настройки ──
    def _on_setting(self, key: str, value) -> None:
        if key == "language":
            apply_language(self.settings)
            self._refresh_ui()
        elif key == "theme":
            theme.apply_theme(value)
            self.setStyleSheet(theme.build_qss())
            self._refresh_ui()
        elif key in ("country_detection", "party_detection", "show_peak_date"):
            self._update_legend()
            self.live_tab.retheme()
            self.post_view.retranslate()
            if key == "party_detection":
                self.service.invalidate()
        elif key == "always_on_top":
            flags = self._flags | (Qt.WindowType.WindowStaysOnTopHint if value else Qt.WindowType(0))
            self.setWindowFlags(flags)
            self.show()

    def _retranslate(self) -> None:
        self.lbl_title.setText(tr("app.title"))
        self.lbl_title.setStyleSheet(f"color: {theme.c.title}; letter-spacing: 1px;")
        self.lbl_status.setStyleSheet(f"color: {theme.c.muted};")
        for key in TAB_KEYS:
            self.nav.set_text(key, tr(f"tab.{key}").upper())
        self.title_bar.retranslate()
        self._render_status()
        self._update_legend()

    def _refresh_ui(self) -> None:
        """Тема или язык сменились: обновляем тексты и перерисовываем всё из сохранённых данных."""
        self._retranslate()
        self.service.invalidate()
        self.live_tab.retheme()
        for t in self._analysis_tabs:
            t.retheme()
        self.tabs["settings"].rebuild()

    # ── выход ──
    def closeEvent(self, event) -> None:
        self.hide()                # окно пропадает мгновенно, поток дозавершается в фоне
        self.worker.stop()
        self.service.shutdown()
        if self.worker.isRunning() and not self.worker.wait(2500):
            self.worker.terminate()
            self.worker.wait(500)
        super().closeEvent(event)
