"""Канонизация ссылок Instagram / YouTube и извлечение идентификатора видео."""

from __future__ import annotations

import re

# --- Instagram ---
SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:[^/?#]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})",
    re.IGNORECASE,
)
# share-ссылки (instagram.com/share/...) shortcode не содержат — их нужно
# разрешить по редиректу до канонической формы (см. resolve_share_url).
SHARE_RE = re.compile(r"instagram\.com/share/", re.IGNORECASE)
IG_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+", re.IGNORECASE)

# --- YouTube (Shorts / youtu.be / watch / embed) ---
YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/shorts/|youtu\.be/|youtube\.com/watch\?(?:[^#]*&)?v=|youtube\.com/embed/)"
    r"([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)
YT_URL_RE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/\S+", re.IGNORECASE)


def extract_shortcode(url: str) -> str | None:
    """Instagram shortcode из ссылки, иначе None."""
    m = SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def extract_youtube_id(url: str) -> str | None:
    """YouTube video id (11 симв.) из ссылки, иначе None."""
    m = YOUTUBE_ID_RE.search(url)
    return m.group(1) if m else None


def parse_video_url(url: str) -> tuple[str, str] | None:
    """(platform, video_id) для поддерживаемых платформ, иначе None.

    platform ∈ {"instagram", "youtube"}.
    """
    yid = extract_youtube_id(url)
    if yid:
        return ("youtube", yid)
    sc = extract_shortcode(url)
    if sc:
        return ("instagram", sc)
    return None


def is_share_url(url: str) -> bool:
    return bool(SHARE_RE.search(url))


def canonical_url(video_id: str, platform: str = "instagram") -> str:
    if platform == "youtube":
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.instagram.com/reel/{video_id}/"


def find_instagram_urls(text: str) -> list[str]:
    """Все поддерживаемые ссылки (Instagram + YouTube) из произвольного текста.

    Имя историческое; теперь находит и YouTube. Сохранено ради существующих вызовов.
    """
    seen: list[str] = []
    for rx in (IG_URL_RE, YT_URL_RE):
        for raw in rx.findall(text):
            url = raw.rstrip(".,;)!?»”'\"")
            if url not in seen:
                seen.append(url)
    return seen


# алиас с понятным именем — предпочтительно использовать его в новом коде
find_video_urls = find_instagram_urls


def resolve_share_url(url: str, proxy: str | None = None) -> str:
    """Разворачивает instagram.com/share/... по редиректу до /reel/<code>/."""
    import httpx

    resp = httpx.get(
        url,
        follow_redirects=True,
        proxy=proxy or None,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    return str(resp.url)
