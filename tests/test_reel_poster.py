from contextlib import nullcontext

import httpx
import pytest

from reels_inbox import pipeline
from reels_inbox.config import AppConfig, Secrets, Settings
from reels_inbox.ledger import Ledger
from reels_inbox.models import AnalysisResult, CardType
from reels_inbox.stages import carousel, download

CODE = "TESTCLIP1"
URL = f"https://www.instagram.com/reel/{CODE}/"
COVER = "https://media.example.cdninstagram.com/cover.jpg"
VIDEO = "https://media.example.cdninstagram.com/video.mp4"


@pytest.fixture
def settings(tmp_path):
    config = Settings(
        secrets=Secrets(apify_token="", reels_proxy=""),
        cfg=AppConfig(
            data_dir=tmp_path / "data",
            vault_path=tmp_path / "vault",
            downloaders=["instagram-embed", "apify"],
        ),
    )
    config.ensure_dirs()
    return config


@pytest.fixture
def embed_poster(monkeypatch):
    def get(url, **kwargs):
        request = httpx.Request("GET", url)
        if url == f"{URL}embed/":
            return httpx.Response(
                200,
                text=(r'{\"display_url\":\"' + COVER
                      + r'\",\"edge_media_to_caption\":{\"edges\":[{\"node\":{\"text\":\"Caption\"}}]}}'),
                request=request,
            )
        assert url == COVER
        return httpx.Response(200, content=b"poster", request=request)

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(download._try_downloader.retry, "sleep", lambda _: None)


def test_video_after_embed_poster_wins(settings, embed_poster, monkeypatch):
    settings.secrets.apify_token = "test-token"
    posts = []

    def post(url, *, json, timeout):
        posts.append(json)
        return httpx.Response(200, json=[{"videoUrl": VIDEO}], request=httpx.Request("POST", url))

    def stream(method, url, **kwargs):
        assert url == VIDEO
        return nullcontext(httpx.Response(200, content=b"video", request=httpx.Request(method, url)))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "stream", stream)
    result, source = download.download_with_fallback(settings, URL, CODE)

    assert source == "apify"
    assert result.video_path.read_bytes() == b"video"
    assert not (settings.downloads_dir / f"{CODE}-cover.jpg").exists()
    assert posts == [{"directUrls": [URL], "resultsLimit": 1}]


@pytest.mark.parametrize("apify", ["disabled", "missing-token", "http-error", "no-items"])
def test_fallback_writes_poster_warning(settings, embed_poster, monkeypatch, apify):
    if apify == "disabled":
        settings.cfg.downloaders.remove("apify")
    if apify != "missing-token":
        settings.secrets.apify_token = "test-token"
    posts = []

    def post(url, **kwargs):
        posts.append(url)
        return httpx.Response(
            503 if apify == "http-error" else 200, json=[], request=httpx.Request("POST", url)
        )

    def analyze_images(self, paths, caption):
        assert [p.read_bytes() for p in paths] == [b"poster"]
        assert caption == "Caption"
        return AnalysisResult(type=CardType.other, title="Тест", summary="Обложка", confidence=0.4), 0.0

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(pipeline.GeminiImagesAnalyzer, "analyze", analyze_images)
    monkeypatch.setattr(pipeline.health, "note_analysis_ok", lambda: None)
    ledger = Ledger(settings.ledger_path)
    try:
        result = pipeline.process(URL, settings, ledger)
    finally:
        ledger.close()

    assert result.card.analysis is not None
    assert "Видео и звук не анализировались." in result.note_path.read_text(encoding="utf-8")
    assert len(posts) == (1 if apify in ("http-error", "no-items") else 0)


@pytest.mark.parametrize(
    ("item", "slides"),
    [
        ({"media_type": 1, "product_type": "clips"}, 0),
        ({"media_type": 1, "is_video": True}, 0),
        ({"media_type": 1, "video_versions": [{"url": VIDEO}]}, 0),
        ({"media_type": 2}, 0),
        ({"media_type": 1}, 1),
        ({"media_type": 8, "carousel_media": [{"media_type": 1}, {"media_type": 2}]}, 2),
    ],
)
def test_carousel_probe_does_not_treat_reel_cover_as_photo(settings, monkeypatch, item, slides):
    image = {"image_versions2": {"candidates": [{"url": COVER}]}}
    item = {**item, **image}
    if "carousel_media" in item:
        item["carousel_media"] = [{**slide, **image} for slide in item["carousel_media"]]
    monkeypatch.setattr(carousel, "_cookies", lambda _: {"sessionid": "test"})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json={"items": [item]}))
    requests = []

    def get_slide(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=b"slide")

    client = httpx.Client(transport=httpx.MockTransport(get_slide))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    with client:
        media = carousel.fetch_post_media(settings, URL, CODE)

    assert (media.slide_count if media else 0) == slides
    assert requests == [COVER] * slides
