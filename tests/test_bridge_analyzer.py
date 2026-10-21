"""Тесты текстового режима Aziz Bridge — без сети и расхода подписки."""

import httpx
import pytest

from reels_inbox.config import AppConfig, Secrets, Settings
from reels_inbox.models import CardType
from reels_inbox.stages.analyze import AnalysisFailed, BridgeTextAnalyzer, _json_text


def _settings(tmp_path):
    return Settings(
        secrets=Secrets(bridge_api_key="bridge_test"),
        cfg=AppConfig(
            data_dir=tmp_path / "data",
            bridge_base_url="http://127.0.0.1:8791/v1",
            bridge_model="gpt-6.1-sol",
        ),
    )


def _result_json():
    return (
        '{"type":"ai-tools","title":"Тест","summary":"Короткое описание",'
        '"key_points":["Пункт"],"tags":["тест"],"interests":["dev"],"confidence":0.4}'
    )


def test_json_text_strips_fence():
    assert _json_text("```json\n{\"ok\": true}\n```") == '{"ok": true}'


def test_bridge_analyzer_sends_only_supported_fields(monkeypatch, tmp_path):
    seen = {}

    def fake_post(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json, timeout=timeout)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"```json\n{_result_json()}\n```"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result, cost = BridgeTextAnalyzer(_settings(tmp_path)).analyze(
        "Короткая подпись", "https://example.test/reel/1"
    )

    assert result.type == CardType.ai_tools
    assert result.title == "Тест"
    assert cost == 0.0
    assert seen["url"] == "http://127.0.0.1:8791/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer bridge_test"
    assert seen["json"].keys() == {"model", "messages", "stream"}
    assert seen["json"]["model"] == "gpt-6.1-sol"
    assert all(set(message) == {"role", "content"} for message in seen["json"]["messages"])


def test_bridge_analyzer_reports_http_error(monkeypatch, tmp_path):
    def fake_post(url, **kwargs):
        return httpx.Response(
            401,
            json={"error": {"message": "Неверный ключ шлюза."}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisFailed, match="HTTP 401"):
        BridgeTextAnalyzer(_settings(tmp_path)).analyze("Подпись", "https://example.test/reel/1")
