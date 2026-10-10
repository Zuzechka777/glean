"""Оркестратор: normalize → ledger check → download → analyze → render → vault → ledger commit.

Принцип: карточка создаётся всегда. Любой сбой понижает полноту, но не
прерывает запись в vault. Статус записывается в frontmatter и в ledger.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .ledger import Ledger
from .models import PipelineContext, ReelCard, Status
from .stages.analyze import (
    is_hard_gemini_error,
    CaptionOnlyAnalyzer,
    GeminiImagesAnalyzer,
    GeminiVideoAnalyzer,
    WhisperGeminiAnalyzer,
)
from .stages.carousel import fetch_post_media
from .stages.download import (
    DownloadFailed,
    UnavailableError,
    download_with_fallback,
)
from .stages.render import write_note
from . import health, retry_queue
from .urls import canonical_url, is_share_url, parse_video_url, resolve_share_url

log = logging.getLogger(__name__)


class Deferred(Exception):
    """Gemini недоступен (квота/страна) — рилс отложен в очередь, карточка не создана."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _hard(msg: str) -> bool:
    """Сигнализировать health и сказать, лечится ли ошибка сейчас."""
    if "RESOURCE_EXHAUSTED" in msg and ("PerDay" in msg or "free_tier" in msg):
        health.note_quota_hit(msg)
    if "location is not supported" in msg:
        health.invalidate_route()
    return is_hard_gemini_error(msg)


class AlreadyProcessed(Exception):
    def __init__(self, note_path: str):
        self.note_path = note_path


class ProcessResult:
    def __init__(self, card: ReelCard, note_path: Path, cost_usd: float):
        self.card = card
        self.note_path = note_path
        self.cost_usd = cost_usd

    def summary_line(self) -> str:
        return (
            f"OK {self.card.type.value} «{self.card.title}» "
            f"→ {self.note_path.relative_to(self.note_path.parents[1])}"
        )


def _resolve_url(url: str, settings: Settings) -> tuple[str, str]:
    """Вернуть (каноническую ссылку, идентификатор). Разрешает IG share-ссылки по редиректу.

    Для YouTube идентификатор неймспейсится как `yt_<id>`, чтобы не пересечься
    с Instagram-shortcode в ledger (общий PRIMARY KEY).
    """
    if is_share_url(url):
        try:
            url = resolve_share_url(url, settings.proxy)
        except Exception as e:
            log.warning("could not resolve share url %s: %s", url, e)
    parsed = parse_video_url(url)
    if not parsed:
        raise ValueError(f"не удалось распознать ссылку на видео (Instagram/YouTube): {url}")
    platform, vid = parsed
    shortcode = vid if platform == "instagram" else f"yt_{vid}"
    return canonical_url(vid, platform), shortcode


def _run_analyzer(ctx: PipelineContext, settings: Settings) -> tuple:
    """Вернуть (AnalysisResult, cost, analyzer_name) или (None, 0, ошибка)."""
    strategy = settings.cfg.analyzer
    try:
        if strategy == "whisper-gemini":
            a = WhisperGeminiAnalyzer(settings)
            res, cost = a.analyze(ctx.video_path, ctx.caption)
            return res, cost, a.name
        else:
            a = GeminiVideoAnalyzer(settings)
            res, cost = a.analyze(ctx.video_path, ctx.caption)
            return res, cost, a.name
    except Exception as e:  # noqa: BLE001  (Gemini 503, сеть и пр. — деградируем, не падаем)
        log.warning("analyzer %s failed: %s", strategy, e)
        return None, 0.0, str(e)


def process(url: str, settings: Settings, ledger: Ledger, force: bool = False) -> ProcessResult:
    """Обработать один рилс. Raises AlreadyProcessed если уже есть и не force."""
    url, shortcode = _resolve_url(url, settings)

    # --- idempotency check ---
    if not force:
        entry = ledger.get(shortcode)
        if entry and entry.status in (Status.inbox, Status.checked, Status.done) and entry.note_path:
            raise AlreadyProcessed(entry.note_path)

    ledger.start_attempt(shortcode, url)

    ctx = PipelineContext(url=url, shortcode=shortcode, force=force)
    total_cost = 0.0
    hard_error: str | None = None

    # --- download: для Instagram сначала пробуем карусель/фото (картинки), иначе видео ---
    media = None
    if "instagram.com" in url:  # YouTube-каруселей не бывает — пробу пропускаем
        try:
            media = fetch_post_media(settings, url, shortcode)
        except Exception as e:  # noqa: BLE001
            log.debug("[%s] carousel probe failed: %s", shortcode, e)

    if media and media.image_paths:
        ctx.image_paths = media.image_paths
        ctx.caption = media.caption
        ctx.author = media.author
        ctx.downloader_used = "ig-carousel"
        log.info("[%s] carousel: %d слайдов", shortcode, len(media.image_paths))
    else:
        try:
            dl_result, dl_name = download_with_fallback(settings, url, shortcode, force=force)
            ctx.video_path = dl_result.video_path
            ctx.image_paths = dl_result.image_paths or []
            ctx.caption = dl_result.caption
            ctx.author = dl_result.author
            ctx.duration_sec = dl_result.duration_sec
            ctx.downloader_used = dl_name
            log.info("[%s] downloaded via %s (%.0fs)", shortcode, dl_name, dl_result.duration_sec or 0)
        except UnavailableError as e:
            log.info("[%s] unavailable: %s", shortcode, e)
            ctx.status = Status.unavailable
            ctx.degradation_note = f"Рилс недоступен: {e}"
        except DownloadFailed as e:
            log.warning("[%s] download failed: %s", shortcode, e)
            ctx.status = Status.failed_download
            ctx.degradation_note = f"Скачать не удалось: {e}"

    # --- analyze ---
    if ctx.image_paths:
        ctx.analyzer_used = "gemini-images"
        try:
            res, cost = GeminiImagesAnalyzer(settings).analyze(ctx.image_paths, ctx.caption)
            total_cost += cost
            ctx.analysis = res
            ctx.status = Status.inbox
            log.info("[%s] carousel analyzed as %s (cost=$%.4f)", shortcode, res.type.value, cost)
        except Exception as e:  # noqa: BLE001  (Gemini 503 и пр. — деградируем, не падаем)
            log.warning("[%s] carousel analysis failed: %s", shortcode, e)
            ctx.degradation_note = (ctx.degradation_note or "") + f" Анализ карусели не удался: {e}"
            if _hard(str(e)):
                hard_error = str(e)
            elif ctx.caption:
                log.info("[%s] carousel → caption-only fallback", shortcode)
                try:
                    res, cost = CaptionOnlyAnalyzer(settings).analyze(ctx.caption, url)
                    total_cost += cost
                    ctx.analysis = res
                    ctx.status = Status.inbox
                    ctx.degradation_note += " [caption-only]"
                except Exception as e2:  # noqa: BLE001
                    log.warning("[%s] caption-only also failed: %s", shortcode, e2)
                    ctx.status = Status.failed_download
            else:
                ctx.status = Status.failed_download
    elif ctx.video_path:
        res, cost, analyzer_name = _run_analyzer(ctx, settings)
        total_cost += cost
        ctx.analyzer_used = analyzer_name
        if res:
            ctx.analysis = res
            ctx.status = Status.inbox
            log.info("[%s] analyzed as %s (cost=$%.4f)", shortcode, res.type.value, cost)
        else:
            ctx.degradation_note = (ctx.degradation_note or "") + f" Анализ не удался: {analyzer_name}"
            if _hard(str(analyzer_name)):
                hard_error = str(analyzer_name)
                log.warning("[%s] quota/location error — caption-only fallback skipped (не жжём квоту)", shortcode)
            elif ctx.caption:
                log.info("[%s] falling back to caption-only analysis", shortcode)
                try:
                    res, cost = CaptionOnlyAnalyzer(settings).analyze(ctx.caption, url)
                    total_cost += cost
                    ctx.analysis = res
                    ctx.status = Status.inbox
                    ctx.degradation_note += " [caption-only]"
                except Exception as e2:  # noqa: BLE001
                    log.warning("[%s] caption-only analysis also failed: %s", shortcode, e2)
    elif ctx.caption and ctx.status not in (Status.unavailable,):
        # видео не скачалось, но caption есть
        log.info("[%s] no video, trying caption-only", shortcode)
        try:
            res, cost = CaptionOnlyAnalyzer(settings).analyze(ctx.caption, url)
            total_cost += cost
            ctx.analysis = res
            ctx.degradation_note = (ctx.degradation_note or "") + " [caption-only, видео не скачалось]"
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] caption-only failed: %s", shortcode, e)
            if _hard(str(e)):
                hard_error = str(e)

    # --- cleanup video/images ---
    if not settings.cfg.keep_video:
        if ctx.video_path:
            try:
                ctx.video_path.unlink(missing_ok=True)
            except Exception:
                pass
        for p in ctx.image_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    # --- build card ---
    now = datetime.now(timezone.utc)
    card = ReelCard(
        shortcode=shortcode,
        source=url,
        status=ctx.status,
        analysis=ctx.analysis,
        caption=ctx.caption,
        author=ctx.author,
        saved=now,
        processed=now,
        note=ctx.degradation_note,
    )

    # --- Gemini недоступен: не пишем пустую карточку, откладываем ---
    if ctx.analysis is None and hard_error:
        retry_queue.add(settings, url, shortcode, hard_error)
        ledger.commit_result(shortcode, Status.failed_download, "", cost_usd=total_cost)
        log.warning("[%s] deferred to retry queue: %s", shortcode, hard_error[:120])
        raise Deferred(hard_error)
    if ctx.analysis is not None:
        health.note_analysis_ok()
        retry_queue.remove(settings, shortcode)

    # --- render & write ---
    note_path = write_note(card, settings)
    ledger.commit_result(shortcode, ctx.status, str(note_path), cost_usd=total_cost)
    return ProcessResult(card, note_path, total_cost)
