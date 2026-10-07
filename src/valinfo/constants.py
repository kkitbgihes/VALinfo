"""Пороги и справочные константы (без зависимостей от Qt). Цвета живут в `ui/theme.py` (темы)."""

THRESHOLDS = {
    "kd":  {"good": 1.15, "bad": 0.85},
    "hs":  {"good": 25.0, "bad": 15.0},
    "adr": {"good": 150.0, "bad": 115.0},
    "acs": {"good": 220.0, "bad": 160.0},
    "wr":  {"good": 55.0,  "bad": 45.0},
}

RANKS = ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ascendant", "Immortal", "Radiant"]

SHARDS = {"latam": "na", "br": "na"}
