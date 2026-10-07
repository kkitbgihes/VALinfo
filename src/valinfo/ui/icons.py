"""QPixmap-провайдер поверх файлового кэша (GUI-поток)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap

from valinfo.storage.assets import AssetStore


class IconProvider:
    def __init__(self, assets: AssetStore):
        self._assets = assets
        self._cache: dict = {}

    def _scaled(self, key, path, size) -> Optional[QPixmap]:
        pix = self._cache.get(key)
        if pix is not None:
            return pix
        if not path.exists():  # None не кэшируем: иконка могла докачаться позже
            return None
        pix = QPixmap(str(path)).scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._cache[key] = pix
        return pix

    def agent(self, agent_uuid, size=28) -> Optional[QPixmap]:
        if not agent_uuid:
            return None
        return self._scaled(("agent", agent_uuid, size), self._assets.agent_path(agent_uuid), size)

    def rank(self, tier_id, size=24) -> Optional[QPixmap]:
        return self._scaled(("rank", tier_id, size), self._assets.rank_path(tier_id), size)

    def flag(self, code, w=24, h=18) -> Optional[QPixmap]:
        """Флаг со скруглёнными углами или None, если файла нет."""
        key = ("flag", code, w, h)
        pix = self._cache.get(key)
        if pix is not None:
            return pix
        path = self._assets.flag_path(code)
        if not path.exists():
            return None
        src = QPixmap(str(path))
        if src.isNull():
            return None
        src = src.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(w, h)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 2.5, 2.5)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, src)
        p.end()
        self._cache[key] = out
        return out
