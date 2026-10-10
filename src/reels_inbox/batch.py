"""Batch-обработка: парсер IG-экспорта saved_posts.json и txt-списка ссылок."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import Settings
from .ledger import Ledger
from .models import Status
from .pipeline import AlreadyProcessed, ProcessResult, process
from .stages.download import AuthError, DownloadFailed
from .urls import find_instagram_urls

log = logging.getLogger(__name__)


def _parse_saved_posts_json(path: Path) -> Iterator[str]:
    """Instagram 'Download Your Information' → saved_posts.json.

    Структура (проверь при реальном экспорте — Meta меняет формат):
    либо список dicts с полем "href", либо вложено в объект.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # бывает {saved_saved_media: [{...}]}
        items = data.get("saved_saved_media") or data.get("saved_media") or []
    else:
        items = []

    for item in items:
        # вложенный объект {string_map_data: {Saved on: {href: ...}}}
        if isinstance(item, dict):
            href = item.get("href")
            if not href:
                for v in item.get("string_map_data", {}).values():
                    if isinstance(v, dict) and v.get("href"):
                        href = v["href"]
                        break
            if href and "instagram.com" in href:
                yield href


def _parse_txt(path: Path) -> Iterator[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            found = find_instagram_urls(line)
            if not found and any(s in line for s in ("instagram.com", "youtube.com", "youtu.be")):
                found = [line]
            yield from found


def parse_batch_file(path: Path) -> list[str]:
    if path.suffix.lower() == ".json":
        urls = list(_parse_saved_posts_json(path))
    else:
        urls = list(_parse_txt(path))
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return seen


@dataclass
class BatchReport:
    total: int = 0
    ok: int = 0
    degraded: int = 0
    failed: int = 0
    skipped: int = 0
    cost_usd: float = 0.0
    auth_stop: bool = False


def run_batch(
    urls: list[str],
    settings: Settings,
    ledger: Ledger,
    limit: int | None = None,
    force: bool = False,
) -> BatchReport:
    report = BatchReport()
    processed = 0

    for url in urls:
        if limit is not None and processed >= limit:
            break

        report.total += 1

        try:
            result: ProcessResult = process(url, settings, ledger, force=force)
        except AlreadyProcessed as e:
            log.info("skip (already processed): %s → %s", url, e.note_path)
            report.skipped += 1
            continue
        except AuthError as e:
            log.error("AUTH ERROR — stopping batch to preserve cookies: %s", e)
            report.failed += 1
            report.auth_stop = True
            break
        except DownloadFailed as e:
            log.warning("download failed for %s: %s", url, e)
            report.failed += 1
        except Exception as e:
            log.exception("unexpected error for %s: %s", url, e)
            report.failed += 1
        else:
            if result.card.status in (Status.failed_download, Status.unavailable):
                report.degraded += 1
            elif result.card.note and "[caption-only" in (result.card.note or ""):
                report.degraded += 1
            else:
                report.ok += 1
            report.cost_usd += result.cost_usd
            processed += 1
            log.info("[%d/%s] %s", processed, limit or "∞", result.summary_line())

        # стоп-лимит расходов
        if report.cost_usd >= settings.cfg.max_cost_per_run:
            log.warning(
                "cost limit $%.2f reached (spent $%.4f) — stopping batch",
                settings.cfg.max_cost_per_run, report.cost_usd,
            )
            break

        # пейсинг между запросами
        if url != urls[-1]:
            lo, hi = settings.cfg.pacing_sec
            pause = random.uniform(lo, hi)
            log.debug("pacing: sleeping %.1fs", pause)
            time.sleep(pause)

    return report
