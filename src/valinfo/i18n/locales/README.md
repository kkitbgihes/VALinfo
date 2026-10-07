# Как добавить язык / How to add a language

1. Скопируйте `en.json` в `<код>.json` (например `de.json`, `uk.json`). / Copy `en.json` to `<code>.json`.
2. В блоке `_meta` поменяйте `code` и `name` — `name` показывается в настройках на самом языке.
   Change `code` and `name` in `_meta`; `name` is shown in Settings in its own language.
3. Переведите значения. **Ключи не меняйте**, подстановки вида `{name}` оставляйте как есть.
   Translate the values. **Do not change keys**; keep `{placeholders}` untouched.
4. Готово: язык появится в «Настройки → Язык». Недостающие ключи автоматически берутся из английского.
   Done: the language appears in Settings → Language. Missing keys fall back to English.

Без пересборки exe можно положить файл сюда / Without rebuilding the exe, drop the file into:
`%LOCALAPPDATA%\ValInfoCache\locales\`

`<br>` внутри строк — перенос строки во всплывающих подсказках. / `<br>` inside strings is a line break in tooltips.
