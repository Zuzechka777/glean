"""Тест парсера IG-экспорта и txt-файла — без сети."""

import json
import textwrap
from pathlib import Path

import pytest
from reels_inbox.batch import parse_batch_file


def test_parse_txt(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text(
        "# комментарий\nhttps://www.instagram.com/reel/AAA111/\n"
        "https://www.instagram.com/reel/BBB222/\n"
        "# ещё комментарий\nhttps://www.instagram.com/reel/AAA111/\n",  # дубль
        encoding="utf-8",
    )
    urls = parse_batch_file(f)
    assert len(urls) == 2
    assert any("AAA111" in u for u in urls)
    assert any("BBB222" in u for u in urls)


def test_parse_saved_posts_json_flat(tmp_path):
    data = [
        {"href": "https://www.instagram.com/reel/JSON001/", "title": "some"},
        {"href": "https://www.instagram.com/reel/JSON002/"},
    ]
    f = tmp_path / "saved_posts.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    urls = parse_batch_file(f)
    assert len(urls) == 2


def test_parse_saved_posts_json_nested(tmp_path):
    data = {
        "saved_saved_media": [
            {
                "title": "Title",
                "string_map_data": {
                    "Saved on": {"href": "https://www.instagram.com/reel/NEST001/", "value": "2026-01-01"}
                }
            }
        ]
    }
    f = tmp_path / "saved_posts.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    urls = parse_batch_file(f)
    assert len(urls) == 1
    assert "NEST001" in urls[0]
