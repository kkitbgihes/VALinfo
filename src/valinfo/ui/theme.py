"""Темы оформления.

Тема = набор цветов (`Theme`). Активная палитра — глобальный объект `c`; виджеты читают цвета как `c.text`,
`c.ally` и т.д. При смене темы `apply_theme()` обновляет палитру, а окно перестраивает виджеты.

Чтобы добавить тему: допишите `Theme(...)` в `THEMES` и перевод `theme.<key>` в locales.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DARK_RANKS = ["#8a8a8a", "#b0763a", "#c0c0c0", "#e8c547", "#3fb6c9", "#b28cf0", "#38c172", "#e05561", "#ffe27a"]
LIGHT_RANKS = ["#6b6b6b", "#8f5a24", "#7d7d7d", "#a67c00", "#16849a", "#7d52c9", "#1f9a58", "#c23a47", "#b8860b"]
DARK_PARTY = ["#C77DFF", "#FFD166", "#06D6A0", "#FF9F1C", "#4D9DE0", "#F72585"]
LIGHT_PARTY = ["#9B4DDB", "#C79A00", "#05A37C", "#E07B00", "#2B7BC4", "#D11A72"]


@dataclass(frozen=True)
class Theme:
    key: str
    bg: str               # фон окна
    card: str             # карточки / таблицы
    header: str           # шапки карточек, поля ввода
    border: str
    ally: str
    enemy: str
    text: str
    muted: str
    title: str            # заголовки и акцентный «золотой» текст
    win: str
    loss: str
    warn: str
    mid: str              # «средние» значения метрик
    accent: str           # активная вкладка, переключатели, выделения
    private: str = "#B28CF0"
    light: bool = False
    ranks: list = field(default_factory=lambda: list(DARK_RANKS))
    party: list = field(default_factory=lambda: list(DARK_PARTY))

    @property
    def hover(self) -> str:
        return _mix(self.header, self.border, 0.55)


def _mix(a: str, b: str, t: float) -> str:
    """Смешивает два #RRGGBB: a*(1-t) + b*t."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * (1 - t) + y * t):02X}" for x, y in zip(pa, pb))


THEMES: dict = {t.key: t for t in (
    Theme("dark", bg="#0b0b0d", card="#18181c", header="#141416", border="#26262A", ally="#2EE6A6",
      enemy="#FF5C5C", text="#F4F4F5", muted="#85858B", title="#FFFFFF", win="#4ADE80", loss="#F2686B",
      warn="#F5B942", mid="#E3C75A", accent="#808080"),
    Theme("daylight", bg="#EEF1F6", card="#FFFFFF", header="#E4E9F1", border="#CBD3E0", ally="#0A8FB0",
          enemy="#D93A4A", text="#1B2330", muted="#667085", title="#1B2330", win="#1B9455", loss="#D13C4B",
          warn="#B86E00", mid="#9A7600", accent="#2F6FED", private="#7D52C9", light=True,
          ranks=LIGHT_RANKS, party=LIGHT_PARTY),
    Theme("midnight", bg="#0B1020", card="#121A2E", header="#1A2440", border="#27345C", ally="#4FC3F7",
          enemy="#FF6B81", text="#E8ECF8", muted="#7C89AD", title="#DCE4FF", win="#3DDC97", loss="#FF5C7A",
          warn="#FFC857", mid="#F2D45C", accent="#5B8CFF"),
    Theme("emerald", bg="#0C1512", card="#121E1A", header="#182A24", border="#25403A", ally="#2EE6A6",
          enemy="#FF7A59", text="#EAF5F0", muted="#77948A", title="#D8EFE5", win="#4ADE80", loss="#F2686B",
          warn="#F5B942", mid="#E3C75A", accent="#2EE6A6"),
    Theme("ember", bg="#140708", card="#1D0C0E", header="#261114", border="#462025", ally="#3EE08F",
      enemy="#FF4D5E", text="#F9EFEF", muted="#9E7C80", title="#FFD9DE", win="#4ADE80", loss="#FF6B6E",
      warn="#F5B942", mid="#E3C75A", accent="#FF4D5E"),
    Theme("violet", bg="#120D1F", card="#1A1330", header="#241A42", border="#392C66", ally="#5CE1FF",
          enemy="#FF5C9E", text="#F2ECFF", muted="#8D81B5", title="#E9DDFF", win="#4EE3A0", loss="#FF5C7C",
          warn="#FFB454", mid="#F0D060", accent="#A877FF"),
    
)}
DEFAULT_THEME = "dark"


class Palette:
    """Активная палитра (атрибуты — те же, что у Theme)."""

    def __init__(self):
        self.apply(THEMES[DEFAULT_THEME])

    def apply(self, t: Theme) -> None:
        self.theme = t
        for name in ("key", "bg", "card", "header", "border", "ally", "enemy", "text", "muted", "title", "win",
                     "loss", "warn", "mid", "accent", "private", "light", "ranks", "party", "hover"):
            setattr(self, name, getattr(t, name))


c = Palette()


def apply_theme(key: str) -> Theme:
    t = THEMES.get(key) or THEMES[DEFAULT_THEME]
    c.apply(t)
    return t


def rgba(color: str, alpha: float) -> str:
    return f"rgba({int(color[1:3], 16)},{int(color[3:5], 16)},{int(color[5:7], 16)},{alpha})"


def qcolor(color: str, alpha: float = 1.0):
    """#RRGGBB + прозрачность → QColor. (QColor НЕ умеет разбирать строку 'rgba(...)': получится невалидный чёрный.)"""
    from PyQt6.QtGui import QColor
    q = QColor(color)
    q.setAlphaF(alpha)
    return q


def build_qss() -> str:
    p = c
    on_accent = "#FFFFFF"
    return f"""
QMainWindow {{ background-color: {p.bg}; }}
QWidget {{ color: {p.text}; font-family: 'Segoe UI', sans-serif; font-size: 12px; }}
QToolTip {{ background-color: {p.header}; color: {p.text}; border: 1px solid {p.border}; padding: 6px 8px; }}

QFrame#CustomTitleBar {{ background-color: {p.bg}; border-bottom: 1px solid {p.border}; }}
QPushButton#TitleBtn, QPushButton#TitleBtnClose {{
    background-color: transparent; border: none; color: {p.muted}; font-size: 12px; font-weight: bold;
    min-width: 32px; max-width: 32px; min-height: 24px; max-height: 24px; border-radius: 3px; padding: 0; }}
QPushButton#TitleBtn:hover {{ background-color: {p.border}; color: {p.text}; }}
QPushButton#TitleBtnClose:hover {{ background-color: {p.loss}; color: #FFFFFF; }}

QFrame#HeaderBar {{ background-color: {p.card}; border-bottom: 2px solid {p.border}; }}
QFrame#TeamCard, QFrame#Card {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 4px; }}
QFrame#CardInner {{ background-color: {p.header}; border: 1px solid {p.border}; border-radius: 6px; }}
QFrame#AllyHeader {{ background-color: {p.header}; border-left: 4px solid {p.ally}; }}
QFrame#EnemyHeader {{ background-color: {p.header}; border-left: 4px solid {p.enemy}; }}
QFrame#NeutralHeader {{ background-color: {p.header}; border-left: 4px solid {p.accent}; }}
QTableWidget {{ background-color: transparent; border: none; gridline-color: {p.border}; outline: 0; }}
QTableWidget::item {{ color: {p.text}; }}
QHeaderView::section {{
    background-color: {p.header}; color: {p.muted}; font-size: 10px; font-weight: bold; border: none;
    border-bottom: 1px solid {p.border}; padding: 4px; }}
QPushButton {{
    background-color: {p.header}; border: 1px solid {p.border}; color: {p.title}; font-weight: bold;
    padding: 6px 12px; border-radius: 3px; }}
QPushButton:hover {{ background-color: {p.hover}; }}

QFrame#NavBar {{ background-color: transparent; border-bottom: 1px solid {p.border}; }}
QPushButton#NavTab {{
    background-color: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
    color: {p.muted}; font-size: 12px; font-weight: bold; padding: 9px 18px; letter-spacing: 1px; }}
QPushButton#NavTab:hover {{ color: {p.text}; background-color: {rgba(p.accent, 0.08)}; }}
QPushButton#NavTab:checked {{ color: {p.title}; border-bottom: 2px solid {p.accent}; background-color: {rgba(p.accent, 0.12)}; }}

QTabWidget::pane {{ border: 1px solid {p.border}; background-color: {p.card}; }}
QTabBar::tab {{
    background-color: {p.header}; color: {p.muted}; padding: 8px 16px; font-weight: bold;
    border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }}
QTabBar::tab:selected {{ background-color: {p.card}; color: {p.title}; border-bottom: 2px solid {p.accent}; }}

QScrollArea {{ background: transparent; border: none; }}
QWidget#qt_scrollarea_viewport {{ background: transparent; }}
QWidget#DuelsBody, QWidget#DuelsGridHost, QWidget#PageBody {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; min-height: 28px; border-radius: 3px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; min-width: 28px; border-radius: 3px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QComboBox {{
    background-color: {p.header}; border: 1px solid {p.border}; border-radius: 4px; padding: 5px 10px;
    color: {p.text}; min-width: 150px; }}
QComboBox:hover {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {p.header}; color: {p.text}; border: 1px solid {p.border};
    selection-background-color: {p.accent}; selection-color: {on_accent}; outline: 0; }}
QLabel {{ background: transparent; }}
"""
