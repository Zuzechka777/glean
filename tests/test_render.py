"""Тесты рендера карточки в Markdown — без сети и без vault."""

import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from reels_inbox.models import (
    AiToolInfo,
    AnalysisResult,
    CardType,
    OfferInfo,
    Place,
    ReelCard,
    Status,
)
from reels_inbox.stages.render import (
    _note_shortcode,
    build_frontmatter,
    note_path_for,
    render_note,
    slugify,
    write_note,
)


def _now():
    return datetime.now(timezone.utc)


def _card(analysis: AnalysisResult, status=Status.inbox) -> ReelCard:
    return ReelCard(
        shortcode="TESTSC001",
        source="https://www.instagram.com/reel/TESTSC001/",
        status=status,
        analysis=analysis,
        author="test_author",
        saved=_now(),
        processed=_now(),
    )


def _analysis(**kwargs) -> AnalysisResult:
    defaults = dict(
        type=CardType.other,
        title="Test title",
        summary="Test summary",
        confidence=0.9,
    )
    return AnalysisResult(**(defaults | kwargs))


# ── slugify ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("5 кафе Тбилиси!", "5-кафе-тбилиси"),
    ("  spaces  ", "spaces"),
    ("", "reel"),
    ("a" * 100, "a" * 60),
])
def test_slugify(title, expected):
    result = slugify(title)
    assert result == expected


# ── frontmatter ──────────────────────────────────────────────────────────────

def test_frontmatter_contains_required_fields():
    card = _card(_analysis(type=CardType.travel, title="Тест"))
    fm = build_frontmatter(card)
    assert "type: travel" in fm
    assert "shortcode: TESTSC001" in fm
    assert "status: inbox" in fm


def test_frontmatter_location_when_coords():
    places = [Place(name="Кафе", what="уютное", lat=41.69, lng=44.80)]
    card = _card(_analysis(type=CardType.travel, places=places))
    fm = build_frontmatter(card)
    assert "location:" in fm
    assert "41.69" in fm


def test_frontmatter_no_location_without_coords():
    places = [Place(name="Кафе", what="уютное", city="Тбилиси")]
    card = _card(_analysis(type=CardType.travel, places=places))
    fm = build_frontmatter(card)
    assert "location:" not in fm


# ── render note ──────────────────────────────────────────────────────────────

def test_render_travel():
    places = [Place(name="Сухишвили", what="Ресторан", city="Тбилиси", lat=41.69, lng=44.80)]
    card = _card(_analysis(
        type=CardType.travel, title="Тбилиси: 3 места", summary="Три топ-кафе.", places=places
    ))
    note = render_note(card)
    assert "---" in note
    assert "Сухишвили" in note
    assert "Google Maps" in note
    assert "geo:41.69,44.8" in note


def test_render_tutorial():
    card = _card(_analysis(type=CardType.tutorial, title="Лайфхак", steps=["Шаг 1", "Шаг 2"]))
    note = render_note(card)
    assert "1. Шаг 1" in note
    assert "2. Шаг 2" in note


def test_render_offer():
    offer = OfferInfo(what="Курс по Python", price="5000 ₽", scam_verdict="Не похоже на скам.", red_flags=[])
    card = _card(_analysis(type=CardType.offer, title="Python-курс", offer=offer))
    note = render_note(card)
    assert "Курс по Python" in note
    assert "Скам-чек" in note


def test_render_recipe():
    card = _card(_analysis(
        type=CardType.recipe, title="Хачапури",
        ingredients=["Мука 500г", "Сыр 300г"], steps=["Замесить тесто", "Запечь"]
    ))
    note = render_note(card)
    assert "Мука 500г" in note
    assert "Замесить тесто" in note


def test_render_ai_tools():
    tool = AiToolInfo(name="Claude", what="LLM", url="https://claude.ai", try_in_5_min="Открой сайт, напиши промпт.")
    card = _card(_analysis(type=CardType.ai_tools, title="Claude", ai_tool=tool))
    note = render_note(card)
    assert "Claude" in note
    assert "Открой сайт" in note


def test_render_degraded_note():
    card = ReelCard(
        shortcode="DEG001",
        source="https://www.instagram.com/reel/DEG001/",
        status=Status.failed_download,
        analysis=None,
        saved=_now(),
        processed=_now(),
        note="Скачать не удалось",
    )
    note = render_note(card)
    assert "Скачать не удалось" in note


def test_render_transcript_collapsed():
    card = _card(_analysis(type=CardType.other, transcript="Привет, я Вася."))
    note = render_note(card)
    assert "Транскрипт" in note
    assert "Привет, я Вася." in note


# ── write_note: дедуп по shortcode (регрессия substring-бага) ─────────────────

def _settings(vault: Path):
    """Минимальный Settings-дубль: write_note/note_path_for читают только cfg.vault_path."""
    return types.SimpleNamespace(cfg=types.SimpleNamespace(vault_path=vault))


def _dedup_card(shortcode: str) -> ReelCard:
    # одинаковые caption+saved → одинаковая база имени, чтобы сработала логика коллизий
    return ReelCard(
        shortcode=shortcode,
        source="s",
        status=Status.inbox,
        caption="title",
        saved=datetime(2024, 1, 1),
        processed=datetime(2024, 1, 1, 12, 0),
    )


def test_write_note_keeps_shortcode_prefix_note(tmp_path):
    """Запись 'ABC' не должна удалять чужую 'ABCD': сравнение по равенству, не подстроке."""
    settings = _settings(tmp_path)

    path_abcd = write_note(_dedup_card("ABCD"), settings)
    path_abc = write_note(_dedup_card("ABC"), settings)

    assert path_abcd.exists()
    assert path_abc.exists()
    assert path_abcd != path_abc  # коллизия имён развела по разным файлам
    assert _note_shortcode(path_abcd) == "ABCD"
    assert _note_shortcode(path_abc) == "ABC"

    # перезапись 'ABC' — substring-баг тут удалял бы 'ABCD' ("ABC" in "ABCD")
    write_note(_dedup_card("ABC"), settings)

    assert path_abcd.exists(), "заметка 'ABCD' не должна удаляться при записи 'ABC'"
    assert _note_shortcode(path_abcd) == "ABCD"


def test_note_shortcode_does_not_raise_on_non_utf8(tmp_path):
    """Посторонний .md в иной кодировке не должен ронять дедуп (errors='ignore')."""
    reels = tmp_path / "Reels"
    reels.mkdir(parents=True)
    binary = reels / "binary.md"
    binary.write_bytes(b"---\nshortcode: \xff\xfe\n---\nbody")

    result = _note_shortcode(binary)  # не должно бросать
    assert result is None or isinstance(result, str)


def test_note_shortcode_returns_none_on_broken_yaml(tmp_path):
    """Битый YAML-frontmatter → None, без исключения."""
    reels = tmp_path / "Reels"
    reels.mkdir(parents=True)
    broken = reels / "broken.md"
    broken.write_text("---\nshortcode: [a, b\n---\nbody", encoding="utf-8")  # незакрытая flow-seq

    assert _note_shortcode(broken) is None
