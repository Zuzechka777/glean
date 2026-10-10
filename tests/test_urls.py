"""Юнит-тесты нормализации URL — без сети."""

import pytest
from reels_inbox.urls import (
    canonical_url,
    extract_shortcode,
    extract_youtube_id,
    find_instagram_urls,
    parse_video_url,
)


@pytest.mark.parametrize("url,expected", [
    ("https://www.instagram.com/reel/CxYZ123abc-/", "CxYZ123abc-"),
    ("https://instagram.com/p/AbCd1234_xy/", "AbCd1234_xy"),
    ("https://www.instagram.com/reels/TESTREEL01/", "TESTREEL01"),
    ("https://www.instagram.com/tv/TVTEST001ab/", "TVTEST001ab"),
    ("https://www.instagram.com/someuser/reel/CODE123xyz/", "CODE123xyz"),
    ("https://t.co/random", None),
    ("https://www.google.com", None),
])
def test_extract_shortcode(url, expected):
    assert extract_shortcode(url) == expected


def test_canonical_url():
    assert canonical_url("ABC123") == "https://www.instagram.com/reel/ABC123/"


def test_canonical_url_youtube():
    assert canonical_url("dQw4w9WgXcQ", "youtube") == "https://www.youtube.com/shorts/dQw4w9WgXcQ"


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/shorts/abcDEF12345", "abcDEF12345"),
    ("https://youtube.com/shorts/abcDEF12345?feature=share", "abcDEF12345"),
    ("https://youtu.be/abcDEF12345", "abcDEF12345"),
    ("https://youtu.be/abcDEF12345?si=xyz", "abcDEF12345"),
    ("https://www.youtube.com/watch?v=abcDEF12345", "abcDEF12345"),
    ("https://m.youtube.com/shorts/abcDEF12345", "abcDEF12345"),
    ("https://www.instagram.com/reel/CxYZ123abc-/", None),
    ("https://www.google.com", None),
])
def test_extract_youtube_id(url, expected):
    assert extract_youtube_id(url) == expected


@pytest.mark.parametrize("url,platform,vid", [
    ("https://www.instagram.com/reel/CxYZ123abc-/", "instagram", "CxYZ123abc-"),
    ("https://www.youtube.com/shorts/abcDEF12345", "youtube", "abcDEF12345"),
    ("https://youtu.be/abcDEF12345", "youtube", "abcDEF12345"),
])
def test_parse_video_url(url, platform, vid):
    assert parse_video_url(url) == (platform, vid)


def test_parse_video_url_unknown():
    assert parse_video_url("https://t.co/random") is None


def test_find_urls_mixed_platforms():
    text = "рилс https://www.instagram.com/reel/XYZ999/ и шортс https://youtube.com/shorts/abcDEF12345 топ"
    urls = find_instagram_urls(text)
    assert len(urls) == 2
    assert any("instagram.com" in u for u in urls)
    assert any("youtube.com" in u for u in urls)


def test_find_instagram_urls_in_text():
    text = "Смотри https://www.instagram.com/reel/XYZ999/ это топ! и ещё раз"
    urls = find_instagram_urls(text)
    assert len(urls) == 1
    assert "XYZ999" in urls[0]


def test_find_instagram_urls_dedup():
    text = "https://www.instagram.com/reel/ABC123/ https://www.instagram.com/reel/ABC123/"
    urls = find_instagram_urls(text)
    assert len(urls) == 1


def test_find_instagram_urls_trailing_punct():
    text = "Смотри https://www.instagram.com/reel/TRAIL123/!"
    urls = find_instagram_urls(text)
    assert urls and "TRAIL123" in urls[0]
    assert not urls[0].endswith("!")
