"""Карусели и фото-посты Instagram.

yt-dlp заточен под видео и не отдаёт картинки каруселей. Берём их через
приватный web-API Instagram (нужны те же cookies, что и для скачивания):
shortcode → media_id → /api/v1/media/{id}/info/ → image_versions2 каждого слайда.

fetch_post_media() возвращает CarouselMedia ТОЛЬКО для постов с картинками
(карусель или одиночное фото). Для одиночного видео возвращает None — такой
пост идёт обычным видео-путём (yt-dlp + аудио/транскрипт).
"""

from __future__ import annotations

import http.cookiejar
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from ..config import Settings

log = logging.getLogger(__name__)

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_IG_APP_ID = "936619743392459"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)


@dataclass
class CarouselMedia:
    image_paths: list[Path] = field(default_factory=list)
    caption: Optional[str] = None
    author: Optional[str] = None
    slide_count: int = 0


def shortcode_to_media_id(shortcode: str) -> int:
    n = 0
    for ch in shortcode:
        n = n * 64 + _ALPHABET.index(ch)
    return n


def _cookies(settings: Settings) -> dict:
    cf = (settings.cfg.cookies_file or "").strip()
    if not cf:
        return {}
    cj = http.cookiejar.MozillaCookieJar(str(Path(cf).expanduser()))
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
    except (FileNotFoundError, http.cookiejar.LoadError) as e:
        log.warning("cookies_file не прочитан (%s): %s", cf, e)
        return {}
    return {c.name: c.value for c in cj}


def _best_image_url(media: dict) -> Optional[str]:
    cands = (media.get("image_versions2") or {}).get("candidates") or []
    return cands[0].get("url") if cands else None


def fetch_post_media(settings: Settings, url: str, shortcode: str) -> Optional[CarouselMedia]:
    """Вернуть CarouselMedia для фото-постов/каруселей, иначе None (видео — мимо)."""
    cookies = _cookies(settings)
    if not cookies:
        return None  # без cookies приватный API недоступен

    media_id = shortcode_to_media_id(shortcode)
    api = f"https://www.instagram.com/api/v1/media/{media_id}/info/"
    headers = {"User-Agent": _UA, "X-IG-App-ID": _IG_APP_ID, "Referer": "https://www.instagram.com/"}

    try:
        resp = httpx.get(api, headers=headers, cookies=cookies, timeout=30,
                         proxy=settings.proxy or None)
    except Exception as e:  # noqa: BLE001
        log.debug("carousel API request failed for %s: %s", shortcode, e)
        return None
    if resp.status_code != 200:
        log.debug("carousel API %s → HTTP %s", shortcode, resp.status_code)
        return None

    try:
        body = resp.json() or {}
    except ValueError:
        # протухшие cookies/challenge → IG отдаёт HTML с кодом 200
        log.debug("carousel API %s вернул не-JSON (cookies протухли?)", shortcode)
        return None
    items = body.get("items") or []
    if not items:
        return None
    item = items[0]
    media_type = item.get("media_type")  # 1=фото, 2=видео, 8=карусель

    # одиночное видео или рилс — пусть качает yt-dlp (там аудио/транскрипт)
    carousel = item.get("carousel_media")
    if media_type == 2 and not carousel:
        return None

    caption = ((item.get("caption") or {}) or {}).get("text")
    author = (item.get("user") or {}).get("username")

    # собрать ссылки на изображения: каждый слайд карусели, либо одно фото
    img_urls: list[str] = []
    if carousel:
        for m in carousel:
            u = _best_image_url(m)  # для видео-слайдов это кадр-обложка
            if u:
                img_urls.append(u)
    elif media_type == 1:
        u = _best_image_url(item)
        if u:
            img_urls.append(u)

    if not img_urls:
        return None

    # скачать все слайды
    paths: list[Path] = []
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": _UA},
                      proxy=settings.proxy or None) as client:
        for i, u in enumerate(img_urls):
            try:
                r = client.get(u)
                r.raise_for_status()
                p = settings.downloads_dir / f"{shortcode}_{i}.jpg"
                p.write_bytes(r.content)
            except (httpx.HTTPError, OSError) as e:
                log.warning("слайд %d не скачался (%s): %s", i, shortcode, e)
                continue
            paths.append(p)

    if not paths:
        return None
    return CarouselMedia(image_paths=paths, caption=caption, author=author,
                         slide_count=len(paths))
