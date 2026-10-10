"""Квота/страна Gemini → рилс уходит в очередь, пустая карточка не пишется. Без сети."""

from types import SimpleNamespace

import pytest
from reels_inbox import pipeline, retry_queue
from reels_inbox.config import AppConfig, Secrets, Settings
from reels_inbox.ledger import Ledger
from reels_inbox.models import Status

URL = "https://www.instagram.com/reel/TESTDEFER1/"

QUOTA = ("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier "
         "generate_content_free_tier_requests")
LOCATION = "400 FAILED_PRECONDITION User location is not supported for the API use."


@pytest.fixture()
def env(tmp_path, monkeypatch):
    settings = Settings(
        secrets=Secrets(gemini_api_key="x"),
        cfg=AppConfig(vault_path=tmp_path / "vault", data_dir=tmp_path / "data"),
    )
    settings.cfg.data_dir.mkdir()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    monkeypatch.setattr(pipeline, "fetch_post_media", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "download_with_fallback", lambda *a, **k: (
        SimpleNamespace(video_path=video, image_paths=None, caption="подпись", author="a", duration_sec=5), "ytdlp"))
    led = Ledger(tmp_path / "ledger.sqlite")
    yield settings, led
    led.close()


@pytest.mark.parametrize("err", [QUOTA, LOCATION])
def test_hard_error_defers(env, monkeypatch, err):
    settings, led = env
    monkeypatch.setattr(pipeline, "_run_analyzer", lambda ctx, s: (None, 0.0, err))
    called = []
    monkeypatch.setattr(pipeline.CaptionOnlyAnalyzer, "analyze",
                        lambda self, *a: called.append(1) or (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(pipeline.Deferred):
        pipeline.process(URL, settings, led, force=True)
    assert not called, "caption-only фолбэк не должен жечь квоту"
    assert not list((settings.cfg.vault_path / "Reels").glob("*.md")), "пустая карточка не пишется"
    assert [i["shortcode"] for i in retry_queue.load(settings)] == ["TESTDEFER1"]
    assert led.get("TESTDEFER1").status == Status.failed_download
