"""Ячейка «Country»: флаг либо понятная иконка, ПОЧЕМУ флага нет.

    флаг            — страна определена
    замок           — профиль на tracker.gg приватный (страну увидеть нельзя)
    пунктирный флаг — профиль открыт, но игрок не указал страну
    ⚠ жёлтый        — сбой алгоритма: Cloudflare / блокировка (повторится само при следующем матче)
    ⚠ красный       — сбой алгоритма: tracker вернул не ту страницу, Edge не найден/упал/таймаут
    …               — матч закончился раньше, чем поиск завершился
    —               — не искали (это вы / ник скрыт / не было данных)
"""
from __future__ import annotations

import html
from functools import lru_cache
from typing import Optional

from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from valinfo.i18n import tr
from valinfo.models import CountryResult, CountryStatus
from valinfo.ui.icons import IconProvider
from valinfo.ui.theme import c
from valinfo.ui.widgets import SpinnerWidget, StatusGlyph, font

def _meta() -> dict:
    """status -> (glyph, цвет, заголовок, пояснение). Функция, а не константа: цвета и язык меняются на лету."""
    return {
        CountryStatus.PRIVATE: ("lock", c.private, tr("country.private.title"), tr("country.private.body")),
        CountryStatus.NO_COUNTRY: ("flag_off", c.muted, tr("country.none.title"), tr("country.none.body")),
        CountryStatus.BLOCKED: ("warning", c.warn, tr("country.blocked.title"), tr("country.blocked.body")),
        CountryStatus.BAD_PAGE: ("warning", c.loss, tr("country.badpage.title"), tr("country.badpage.body")),
        CountryStatus.ERROR: ("warning", c.loss, tr("country.error.title"), tr("country.error.body")),
        CountryStatus.INTERRUPTED: ("dots", c.muted, tr("country.interrupted.title"), tr("country.interrupted.body")),
    }


@lru_cache(maxsize=None)
def country_name(code: str) -> str:
    try:
        territory = QLocale.codeToTerritory(code)
        if territory != QLocale.Country.AnyCountry:
            name = QLocale.territoryToString(territory)
            if name:
                return name
    except Exception:
        pass
    return code


def _tip(title: str, body: str = "", detail: Optional[str] = None) -> str:
    parts = [f"<b>{title}</b>"]
    if body:
        parts.append(body)
    if detail:
        parts.append(f"<span style='color:{c.muted};'>{html.escape(detail)}</span>")
    return "<br>".join(parts)


def build_country_cell(result: Optional[CountryResult], icons: IconProvider) -> QWidget:
    """result=None → пустая ячейка (страну для этой строки не ищем)."""
    widget = QWidget()
    lay = QHBoxLayout(widget)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if result is None:
        return widget

    st = result.status

    if st is CountryStatus.LOADING:
        lay.addWidget(SpinnerWidget(16, c.warn))
        widget.setToolTip(_tip(tr("country.loading")))

    elif st is CountryStatus.FOUND:
        lbl = QLabel()
        pix = icons.flag(result.code, 24, 18) if result.code else None
        if pix:
            lbl.setPixmap(pix)
        else:  # флаг не скачался — показываем код страны текстом
            lbl.setText(result.code or "?")
            lbl.setFont(font(9, True))
            lbl.setStyleSheet(f"color: {c.title};")
        lay.addWidget(lbl)
        code = result.code or "?"
        widget.setToolTip(_tip(html.escape(country_name(code)), detail=code))

    elif st in (meta := _meta()):
        kind, color, title, body = meta[st]
        lay.addWidget(StatusGlyph(kind, color, 18))
        widget.setToolTip(_tip(title, body, result.detail if st.is_failure or st is CountryStatus.INTERRUPTED else None))

    else:  # SKIPPED и всё неизвестное
        lbl = QLabel("—")
        lbl.setFont(font(10, True))
        lbl.setStyleSheet(f"color: {c.muted};")
        lay.addWidget(lbl)
        if result.detail:
            widget.setToolTip(_tip(tr("country.skipped"), html.escape(result.detail)))
    return widget


def build_legend() -> QWidget:
    """Компактная легенда для шапки: что означают иконки в колонке Country."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    cap = QLabel(tr("country.legend.caption"))
    cap.setFont(font(8, True))
    cap.setStyleSheet(f"color: {c.muted};")
    lay.addWidget(cap)
    for kind, color, key in (("lock", c.private, "country.private.title"), ("flag_off", c.muted, "country.none.title"),
                             ("warning", c.warn, "country.legend.failed")):
        lay.addSpacing(6)
        lay.addWidget(StatusGlyph(kind, color, 14))
        t = QLabel(tr(key))
        t.setFont(font(8, False))
        t.setStyleSheet(f"color: {c.muted};")
        lay.addWidget(t)
    w.setToolTip(_tip(tr("country.legend.title"), tr("country.legend.body")))
    return w
