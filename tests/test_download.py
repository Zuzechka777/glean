import pytest

from reels_inbox.stages.download import (
    DownloadFailed,
    UnavailableError,
    extract_embed_metadata,
    extract_embed_video_url,
    netscape_cookie_names,
)


def test_extract_embed_video_url_decodes_json_escaping():
    html = r'{"video_url":"https:\/\/scontent.example.cdninstagram.com\/v\/clip.mp4?a=1\u0026b=2"}'
    assert extract_embed_video_url(html) == (
        "https://scontent.example.cdninstagram.com/v/clip.mp4?a=1&b=2"
    )


def test_extract_embed_video_url_decodes_nested_instagram_json():
    html = r'{\"video_url\":\"https:\\\/\\\/scontent.example.cdninstagram.com\\\/v\\\/clip.mp4?a=1\"}'
    assert extract_embed_video_url(html) == (
        "https://scontent.example.cdninstagram.com/v/clip.mp4?a=1"
    )


def test_extract_embed_video_url_rejects_unknown_host():
    html = r'{"video_url":"https:\/\/example.com\/clip.mp4"}'
    with pytest.raises(DownloadFailed, match="unexpected video host"):
        extract_embed_video_url(html)


def test_extract_embed_video_url_requires_video():
    with pytest.raises(UnavailableError, match="no video_url"):
        extract_embed_video_url("<html>no video</html>")


def test_extract_embed_metadata_from_nested_instagram_json():
    html = (
        r'{\"display_url\":\"https:\\\/\\\/scontent.example.cdninstagram.com\\\/cover.jpg\",'
        r'\"video_duration\":39.936,'
        r'\"edge_media_to_caption\":{\"edges\":[{\"node\":{\"text\":\"Comment \\u201cDOCTOR\\u201d\"}}]},'
        r'\"owner\":{\"id\":\"1\",\"username\":\"devinkarpes\"}}'
    )
    metadata = extract_embed_metadata(html)
    assert metadata.display_url == "https://scontent.example.cdninstagram.com/cover.jpg"
    assert metadata.caption == "Comment “DOCTOR”"
    assert metadata.author == "devinkarpes"
    assert metadata.duration_sec == pytest.approx(39.936)


def test_netscape_cookie_names_includes_httponly(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n"
        ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tsecret\n",
        encoding="utf-8",
    )
    assert netscape_cookie_names(cookies) == {"sessionid", "csrftoken"}
