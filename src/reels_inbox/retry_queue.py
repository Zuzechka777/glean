"""Отложенные рилсы: Gemini упёрся в квоту/страну → карточку не пишем, ждём."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from .config import Settings

_lock = threading.Lock()


def _path(settings: Settings) -> Path:
    return settings.cfg.data_dir / "retry_queue.json"


def load(settings: Settings) -> list[dict]:
    try:
        return json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(settings: Settings, items: list[dict]) -> None:
    _path(settings).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def add(settings: Settings, url: str, shortcode: str, reason: str) -> None:
    with _lock:
        items = [i for i in load(settings) if i["shortcode"] != shortcode]
        items.append({"url": url, "shortcode": shortcode, "reason": reason[:200],
                      "added": datetime.now().strftime("%Y-%m-%d %H:%M")})
        _save(settings, items)


def remove(settings: Settings, shortcode: str) -> None:
    with _lock:
        _save(settings, [i for i in load(settings) if i["shortcode"] != shortcode])
