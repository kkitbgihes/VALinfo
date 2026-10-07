import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Тесты не должны трогать настоящие настройки пользователя
from valinfo import settings as _settings  # noqa: E402

_tmp = tempfile.TemporaryDirectory()
_settings.set_settings(_settings.Settings(Path(_tmp.name) / "settings.json"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_global_ui_state():
    """Язык и тема — глобальное состояние; каждый тест стартует с английского и темы по умолчанию."""
    from valinfo.i18n import translator
    from valinfo.ui import theme
    translator.set_language("en")
    theme.apply_theme("valorant")
    yield
