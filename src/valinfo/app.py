"""Точка входа приложения."""
from __future__ import annotations

import ctypes
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from valinfo.logging_setup import setup_logging
from valinfo.paths import ensure_dirs, resource_path
from valinfo.storage.assets import AssetStore
from valinfo.ui.main_window import MainWindow


def main() -> int:
    ensure_dirs()
    setup_logging()

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("valinfo.tracker.app")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("assets/icon.ico")))

    window = MainWindow(AssetStore())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
