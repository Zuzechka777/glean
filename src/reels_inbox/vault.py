"""Инициализация Obsidian vault: папки + дашборды Dataview/Map View."""

from __future__ import annotations

from pathlib import Path


INBOX_DASH = """\
# Inbox — новые рилсы

> [!tip] Смени `status: checked` когда проверил, `status: done` когда воспользовался.

```dataview
TABLE author, type, saved
FROM "Reels"
WHERE status = "inbox"
SORT saved DESC
```
"""

BY_TYPE_DASH = """\
# По типу

## Путешествия
```dataview
TABLE author, title, saved
FROM "Reels"
WHERE type = "travel" AND status != "unavailable"
SORT saved DESC
```

## Туториалы и лайфхаки
```dataview
TABLE author, title, saved
FROM "Reels"
WHERE type = "tutorial" AND status != "unavailable"
SORT saved DESC
```

## Офферы и сервисы
```dataview
TABLE author, title, saved
FROM "Reels"
WHERE type = "offer" AND status != "unavailable"
SORT saved DESC
```

## Рецепты
```dataview
TABLE author, title, saved
FROM "Reels"
WHERE type = "recipe" AND status != "unavailable"
SORT saved DESC
```

## AI-инструменты
```dataview
TABLE author, title, saved
FROM "Reels"
WHERE type = "ai-tools" AND status != "unavailable"
SORT saved DESC
```
"""

TRAVEL_MAP_DASH = """\
# Карта мест из рилсов

> [!note] Плагин **Map View** должен быть установлен (Settings → Community plugins → Map View).
> Каждая travel-карточка с `location:` в frontmatter появится как метка.

```mapview
{"name":"Все travel-рилсы","query":"path:\\"Reels\\" AND [\\\"type\\":travel]","chosenMapSource":0,"showLinks":false,"nodeMinSize":10,"nodeMaxSize":30,"lineSizeMultiplier":1,"nodeInteractWithEditor":false,"showNeighboringFiles":false,"showFolderNodes":false}
```
"""

PROBLEMS_DASH = """\
# Проблемные — на retry

```dataview
TABLE url AS "Ссылка", status, note AS "Причина", saved
FROM "Reels"
WHERE status = "failed-download" OR status = "unavailable"
SORT saved DESC
```

Чтобы повторить обработку: скопируй ссылки и запусти
```
reels-inbox retry --status failed-download
```
"""


def init_vault(vault_path: Path) -> list[Path]:
    """Создать структуру vault + dashboard-заметки. Существующие файлы не перезаписываем."""
    created: list[Path] = []

    for folder in ("Reels", "Dashboards", "attachments"):
        (vault_path / folder).mkdir(parents=True, exist_ok=True)

    dashboards = {
        "00 Inbox.md": INBOX_DASH,
        "10 By Type.md": BY_TYPE_DASH,
        "20 Travel Map.md": TRAVEL_MAP_DASH,
        "90 Problems.md": PROBLEMS_DASH,
    }
    for name, content in dashboards.items():
        p = vault_path / "Dashboards" / name
        if not p.exists():
            p.write_text(content, encoding="utf-8", newline="\n")
            created.append(p)

    return created
