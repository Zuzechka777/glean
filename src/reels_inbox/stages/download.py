"""Скачивание рилса: yt-dlp (cookies burner-аккаунта) с фолбэком на Apify.

Классификация ошибок:
- AuthError    — «login required» / rate limit: пробуем следующий загрузчик,
                 в batch-режиме останавливаем пачку, чтобы не дожигать cookies;
- UnavailableError — приватный/удалённый рилс: ретраи бессмысленны;
- DownloadFailed   — всё остальное (сеть и т.п.), подлежит retry.
"""

from __future__ import annotations

import abc
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ..config import Settings

log = logging.getLogger(__name__)


class DownloadFailed(Exception):
    pass


class AuthError(DownloadFailed):
    pass


class UnavailableError(DownloadFailed):
    pass


@dataclass
class DownloadResult:
    video_path: Optional[Path]
    caption: Optional[str]
    author: Optional[str]
    duration_sec: Optional[float]
    image_paths: Optional[list[Path]] = None


AUTH_MARKERS = ("login required", "rate-limit", "rate limit", "429", "checkpoint_required", "requested content is not available")
UNAVAILABLE_MARKERS = ("private", "not available", "removed", "unavailable", "404", "no longer exists")


def classify_ytdlp_error(message: str) -> type[DownloadFailed]:
    msg = message.lower()
    if any(m in msg for m in AUTH_MARKERS):
        return AuthError
    if any(m in msg for m in UNAVAILABLE_MARKERS):
        return UnavailableError
    return DownloadFailed


class Downloader(abc.ABC):
    name: str

    def __init__(self, settings: Settings):
        self.settings = settings

    @abc.abstractmethod
    def download(self, url: str, shortcode: str, force: bool = False) -> DownloadResult: ...


def netscape_cookie_names(path: Path) -> set[str]:
    """Вернуть только имена cookies из Netscape-файла, не читая значения наружу."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return set()
    names: set[str] = set()
    for line in lines:
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            names.add(parts[5])
    return names


class YtDlpDownloader(Downloader):
    name = "ytdlp"

    def download(self, url: str, shortcode: str, force: bool = False) -> DownloadResult:
        import yt_dlp

        out_path = self.settings.downloads_dir / f"{shortcode}.mp4"
        # cookies.txt имеет приоритет: обходит расшифровку cookies браузера (macOS Keychain).
        cookie_file = (self.settings.cfg.cookies_file or "").strip()
        browser_spec = self.settings.cfg.cookies_from_browser
        # формат yt-dlp: ("chrome",) или ("chrome", "Profile 2")
        browser = tuple(p.strip() for p in browser_spec.split(":", 1)) if browser_spec else None

        opts = {
            "outtmpl": str(self.settings.downloads_dir / f"{shortcode}.%(ext)s"),
            "format": "mp4/bv*+ba/b",
            "merge_output_format": "mp4",
            "quiet": True,
            "noprogress": True,
            "noplaylist": True,
            "break_on_existing": False,
            "overwrites": True,
        }
        # archive блокирует повторное скачивание; при --force его не используем
        if not force:
            opts["download_archive"] = str(self.settings.archive_path)
        if cookie_file:
            cookie_path = Path(cookie_file).expanduser()
            if "instagram.com" in url and "sessionid" not in netscape_cookie_names(cookie_path):
                raise AuthError(
                    "cookies.txt has no Instagram sessionid; skipping authenticated yt-dlp"
                )
            opts["cookiefile"] = str(cookie_path)
        elif browser:
            opts["cookiesfrombrowser"] = browser
        if self.settings.proxy:
            opts["proxy"] = self.settings.proxy

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            raise classify_ytdlp_error(str(e))(str(e)) from e

        if not out_path.exists():
            # extract_info мог отдать другой контейнер
            candidates = list(self.settings.downloads_dir.glob(f"{shortcode}.*"))
            video_files = [c for c in candidates if c.suffix in (".mp4", ".webm", ".mkv", ".mov")]
            if not video_files:
                raise DownloadFailed(f"yt-dlp finished but no video file for {shortcode}")
            out_path = video_files[0]

        return DownloadResult(
            video_path=out_path,
            caption=(info or {}).get("description"),
            author=(info or {}).get("uploader") or (info or {}).get("channel"),
            duration_sec=(info or {}).get("duration"),
        )


_EMBED_VIDEO_RE = re.compile(r'"video_url"\s*:\s*"((?:\\.|[^"\\])+)"')
_EMBED_NESTED_VIDEO_RE = re.compile(r'\\"video_url\\":\\"(.*?)\\"')
_EMBED_DISPLAY_RE = re.compile(r'\\"display_url\\":\\"(.*?)\\"')
_EMBED_CAPTION_RE = re.compile(
    r'\\"edge_media_to_caption\\":\{\\"edges\\":\[\{\\"node\\":\{\\"text\\":\\"(.*?)\\"'
)
_EMBED_OWNER_RE = re.compile(r'\\"owner\\":\{.*?\\"username\\":\\"(.*?)\\"')
_EMBED_DURATION_RE = re.compile(r'\\"video_duration\\":([0-9.]+)')


@dataclass
class EmbedMetadata:
    display_url: Optional[str]
    caption: Optional[str]
    author: Optional[str]
    duration_sec: Optional[float]


def _decode_nested_json_string(value: str, levels: int = 2) -> str:
    decoded = value
    for _ in range(levels):
        decoded = json.loads(f'"{decoded}"')
    return decoded


def _validate_instagram_cdn_url(url: str, field: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    allowed = host.endswith(".cdninstagram.com") or host.endswith(".fbcdn.net")
    if not url.startswith("https://") or not allowed:
        raise DownloadFailed(f"instagram embed returned unexpected {field} host: {host or 'empty'}")
    return url


def extract_embed_metadata(page_html: str) -> EmbedMetadata:
    """Извлечь публичные caption/автора/обложку из embed без cookies."""
    display_match = _EMBED_DISPLAY_RE.search(page_html)
    caption_match = _EMBED_CAPTION_RE.search(page_html)
    owner_match = _EMBED_OWNER_RE.search(page_html)
    duration_match = _EMBED_DURATION_RE.search(page_html)

    display_url = None
    if display_match:
        try:
            display_url = _validate_instagram_cdn_url(
                _decode_nested_json_string(display_match.group(1)), "image"
            )
        except json.JSONDecodeError as e:
            raise DownloadFailed("instagram embed returned malformed display_url") from e

    def decode_optional(match: Optional[re.Match[str]]) -> Optional[str]:
        if not match:
            return None
        try:
            return _decode_nested_json_string(match.group(1))
        except json.JSONDecodeError:
            return None

    return EmbedMetadata(
        display_url=display_url,
        caption=decode_optional(caption_match),
        author=decode_optional(owner_match),
        duration_sec=float(duration_match.group(1)) if duration_match else None,
    )


def extract_embed_video_url(page_html: str) -> str:
    """Достать прямой MP4 из публичной Instagram embed-страницы.

    Instagram хранит URL JSON-экранированным внутри HTML. Ограничиваем
    допустимые CDN-хосты, чтобы случайная строка из страницы не превратила
    загрузчик в произвольный HTTP-клиент.
    """
    match = _EMBED_VIDEO_RE.search(page_html)
    nested = False
    if not match:
        match = _EMBED_NESTED_VIDEO_RE.search(page_html)
        nested = bool(match)
    if not match:
        raise UnavailableError("instagram embed has no video_url")
    try:
        video_url = json.loads(f'"{match.group(1)}"')
        if nested:
            # На реальной embed-странице JSON лежит строкой внутри другого JSON,
            # поэтому слэши экранированы дважды.
            video_url = json.loads(f'"{video_url}"')
    except json.JSONDecodeError as e:
        raise DownloadFailed("instagram embed returned malformed video_url") from e

    return _validate_instagram_cdn_url(video_url, "video")


class InstagramEmbedDownloader(Downloader):
    """Публичный fallback для Reels без cookies.

    Embed-страница Instagram часто остаётся доступной, когда приватный API,
    используемый yt-dlp, отвечает empty media response. Подходит только для
    видео; карусели по-прежнему обрабатывает отдельный carousel-stage.
    """

    name = "instagram-embed"

    def download(self, url: str, shortcode: str, force: bool = False) -> DownloadResult:
        embed_url = f"https://www.instagram.com/reel/{shortcode}/embed/"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"}
        try:
            page = httpx.get(embed_url, headers=headers, timeout=30, follow_redirects=True)
            page.raise_for_status()
            metadata = extract_embed_metadata(page.text)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 410):
                raise UnavailableError(f"instagram embed returned {e.response.status_code}") from e
            raise DownloadFailed(f"instagram embed page failed: {e}") from e
        except httpx.HTTPError as e:
            raise DownloadFailed(f"instagram embed page failed: {e}") from e

        try:
            video_url = extract_embed_video_url(page.text)
        except UnavailableError:
            if not metadata.display_url:
                raise
            # Instagram иногда скрывает MP4 у незалогиненных, но оставляет
            # обложку, caption и автора. Этого достаточно для честного
            # image+caption анализа вместо технической заглушки.
            image_path = self.settings.downloads_dir / f"{shortcode}-cover.jpg"
            try:
                response = httpx.get(
                    metadata.display_url, headers=headers, timeout=60, follow_redirects=True
                )
                response.raise_for_status()
                image_path.write_bytes(response.content)
            except httpx.HTTPError as e:
                raise DownloadFailed(f"instagram embed cover download failed: {e}") from e
            if image_path.stat().st_size == 0:
                raise DownloadFailed("instagram embed downloaded an empty cover")
            return DownloadResult(
                video_path=None,
                caption=metadata.caption,
                author=metadata.author,
                duration_sec=metadata.duration_sec,
                image_paths=[image_path],
            )

        out_path = self.settings.downloads_dir / f"{shortcode}.mp4"
        try:
            with httpx.stream(
                "GET", video_url, headers=headers, timeout=300, follow_redirects=True
            ) as response:
                response.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in response.iter_bytes(1 << 16):
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise DownloadFailed(f"instagram embed video download failed: {e}") from e

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise DownloadFailed("instagram embed downloaded an empty video")
        return DownloadResult(
            video_path=out_path,
            caption=metadata.caption,
            author=metadata.author,
            duration_sec=metadata.duration_sec,
        )


class ApifyDownloader(Downloader):
    """Фолбэк: actor apify/instagram-reel-scraper. Не требует cookies и доступа к IG с машины.

    ВНИМАНИЕ: имена полей input/output актора проверить на тестовой пачке 5-10 рилсов
    (см. SETUP-MAC.md) — Apify периодически меняет схему, а цена с videoUrl-событиями
    может быть ~$0.10/рилс.
    """

    name = "apify"
    ACTOR = "apify~instagram-reel-scraper"

    def download(self, url: str, shortcode: str, force: bool = False) -> DownloadResult:
        token = self.settings.secrets.apify_token
        if not token:
            raise DownloadFailed("APIFY_TOKEN is not set; cannot use apify fallback")

        run_url = (
            f"https://api.apify.com/v2/acts/{self.ACTOR}/run-sync-get-dataset-items"
            f"?token={token}"
        )
        payload = {"directUrls": [url], "resultsLimit": 1}
        resp = httpx.post(run_url, json=payload, timeout=300)
        resp.raise_for_status()
        items = resp.json()
        if not items:
            raise UnavailableError(f"apify returned no items for {url}")
        item = items[0]
        if item.get("error"):
            err = str(item["error"])
            raise classify_ytdlp_error(err)(f"apify item error: {err}")

        video_url = item.get("videoUrl") or item.get("video_url")
        if not video_url:
            raise DownloadFailed(f"apify item has no videoUrl: keys={sorted(item.keys())}")

        out_path = self.settings.downloads_dir / f"{shortcode}.mp4"
        with httpx.stream("GET", video_url, timeout=300, follow_redirects=True) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)

        return DownloadResult(
            video_path=out_path,
            caption=item.get("caption"),
            author=item.get("ownerUsername") or item.get("owner_username"),
            duration_sec=item.get("videoDuration"),
        )


DOWNLOADERS: dict[str, type[Downloader]] = {
    "ytdlp": YtDlpDownloader,
    "instagram-embed": InstagramEmbedDownloader,
    "apify": ApifyDownloader,
}


@retry(
    # Auth/unavailable не исправятся повтором через 5 секунд. Сетевые и прочие
    # обычные DownloadFailed по-прежнему получают вторую попытку.
    retry=retry_if_exception(lambda e: type(e) is DownloadFailed),
    stop=stop_after_attempt(2),
    wait=wait_exponential_jitter(initial=5, max=30),
    reraise=True,
)
def _try_downloader(dl: Downloader, url: str, shortcode: str, force: bool = False) -> DownloadResult:
    return dl.download(url, shortcode, force=force)


def _short(msg: object, limit: int = 200) -> str:
    text = " ".join(str(msg).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def download_with_fallback(
    settings: Settings, url: str, shortcode: str, force: bool = False
) -> tuple[DownloadResult, str]:
    """Идёт по цепочке загрузчиков из config; возвращает (результат, имя загрузчика).

    Обложка без видео остаётся запасным результатом до конца цепочки.
    Даже при UnavailableError пробуем следующий загрузчик: yt-dlp иногда
    рапортует «недоступно» там, где Apify через свою прокси-сеть достаёт видео.
    Если упала вся цепочка — наружу уходит сводка ВСЕХ ошибок (последняя может
    быть тривиальной вроде «APIFY_TOKEN is not set» и маскировать настоящую),
    с классом самой значимой: AuthError > UnavailableError > DownloadFailed.
    """
    failures: list[tuple[str, Exception]] = []
    poster: tuple[DownloadResult, str] | None = None
    for name in settings.cfg.downloaders:
        dl_cls = DOWNLOADERS.get(name)
        if not dl_cls:
            log.warning("unknown downloader %r in config, skipping", name)
            continue
        dl = dl_cls(settings)
        try:
            result = _try_downloader(dl, url, shortcode, force=force)
            if result.video_path is None and result.image_paths:
                if poster is None:
                    poster = (result, dl.name)
                log.info("downloader %s: poster only, trying next", name)
                continue
            if poster and not settings.cfg.keep_video:
                for path in poster[0].image_paths or []:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        log.debug("could not remove unused poster %s", path)
            return result, dl.name
        except UnavailableError as e:
            failures.append((name, e))
            log.info("downloader %s: unavailable (%s)", name, e)
            continue
        except AuthError as e:
            failures.append((name, e))
            log.warning("downloader %s: auth/rate-limit (%s), trying next", name, e)
            continue
        except (DownloadFailed, httpx.HTTPError) as e:
            failures.append((name, e))
            log.warning("downloader %s failed (%s), trying next", name, e)
            continue

    if poster is not None:
        return poster
    assert failures
    if len(failures) == 1:
        err = failures[0][1]
        raise err if isinstance(err, DownloadFailed) else DownloadFailed(str(err))
    cls: type[DownloadFailed] = DownloadFailed
    if any(isinstance(e, AuthError) for _, e in failures):
        cls = AuthError
    elif any(isinstance(e, UnavailableError) for _, e in failures):
        cls = UnavailableError
    raise cls(" | ".join(f"{n}: {_short(e)}" for n, e in failures))
