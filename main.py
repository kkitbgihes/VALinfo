"""Запуск из исходников: `python main.py` (или `python -m valinfo` после `pip install -e .`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from valinfo.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
