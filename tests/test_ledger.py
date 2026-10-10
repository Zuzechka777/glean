"""Тесты ledger — на временной SQLite, без сети."""

import tempfile
from pathlib import Path

import pytest
from reels_inbox.ledger import Ledger
from reels_inbox.models import Status


@pytest.fixture()
def ledger(tmp_path):
    db = Ledger(tmp_path / "ledger.sqlite")
    yield db
    db.close()


def test_start_and_commit(ledger):
    ledger.start_attempt("TESTCODE1", "https://www.instagram.com/reel/TESTCODE1/")
    entry = ledger.get("TESTCODE1")
    assert entry is not None
    assert entry.attempts == 1
    assert entry.status == Status.failed_download  # промежуточный статус

    ledger.commit_result("TESTCODE1", Status.inbox, "/tmp/note.md", cost_usd=0.005)
    entry = ledger.get("TESTCODE1")
    assert entry.status == Status.inbox
    assert entry.note_path == "/tmp/note.md"
    assert entry.cost_usd == pytest.approx(0.005)


def test_idempotent_start(ledger):
    ledger.start_attempt("CODE2", "https://www.instagram.com/reel/CODE2/")
    ledger.start_attempt("CODE2", "https://www.instagram.com/reel/CODE2/")
    entry = ledger.get("CODE2")
    assert entry.attempts == 2


def test_pending(ledger):
    ledger.start_attempt("FAIL1", "https://www.instagram.com/reel/FAIL1/")
    ledger.start_attempt("FAIL2", "https://www.instagram.com/reel/FAIL2/")
    ledger.start_attempt("FAIL3", "https://www.instagram.com/reel/FAIL3/")
    # FAIL3 — слишком много попыток
    ledger.start_attempt("FAIL3", "https://www.instagram.com/reel/FAIL3/")
    ledger.start_attempt("FAIL3", "https://www.instagram.com/reel/FAIL3/")

    pending = ledger.pending([Status.failed_download], max_attempts=3)
    codes = {e.shortcode for e in pending}
    assert "FAIL1" in codes
    assert "FAIL2" in codes
    assert "FAIL3" not in codes  # 3 попытки исчерпаны (>= max)


def test_stats(ledger):
    ledger.start_attempt("A", "https://www.instagram.com/reel/A/")
    ledger.commit_result("A", Status.inbox, "/v/a.md", cost_usd=0.01)
    ledger.start_attempt("B", "https://www.instagram.com/reel/B/")
    # B остаётся failed-download

    s = ledger.stats()
    assert s["total"] == 2
    assert s["by_status"].get("inbox", 0) == 1
    assert s["total_cost_usd"] == pytest.approx(0.01)
