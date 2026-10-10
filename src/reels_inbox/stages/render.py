"""Рендер карточки в Markdown-заметку Obsidian: YAML frontmatter + тело по шаблону типа."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import PROJECT_ROOT, Settings
from ..models import CardType, ReelCard

TEMPLATES_DIR = PROJECT_ROOT / "templates"

TEMPLATE_BY_TYPE = {
    CardType.travel: "travel.md.j2",
    CardType.tutorial: "tutorial.md.j2",
    CardType.offer: "offer.md.j2",
    CardType.recipe: "recipe.md.j2",
    CardType.ai_tools: "ai_tools.md.j2",
    CardType.other: "fallback.md.j2",
}

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_DASH_RE = re.compile(r"[\s_]+")


def slugify(title: str, max_len: int = 60) -> str:
    """Кириллица в имени файла допустима (APFS/Obsidian ок), чистим только мусор."""
    s = _SLUG_STRIP_RE.sub("", title).strip().lower()
    s = _SLUG_DASH_RE.sub("-", s).strip("-")
    return s[:max_len].rstrip("-") or "reel"


def build_frontmatter(card: ReelCard) -> str:
    fm: dict = {
        "type": card.type.value,
        "status": card.status.value,
        "title": card.title,
        "source": card.source,
        "shortcode": card.shortcode,
        "author": f"@{card.author}" if card.author and not card.author.startswith("@") else card.author,
        "saved": card.saved.strftime("%Y-%m-%d"),
        "processed": card.processed.strftime("%Y-%m-%dT%H:%M"),
        "tags": ["reels"] + (card.analysis.tags if card.analysis else []),
        "interests": (card.analysis.interests or None) if card.analysis else None,
        "used": False,  # владелец отмечает «использовано» в веб-UI / Obsidian
    }
    if card.analysis and card.analysis.places:
        first = next(
            (p for p in card.analysis.places if p.lat is not None and p.lng is not None), None
        )
        if first:
            fm["location"] = [first.lat, first.lng]  # метка для Map View
    fm = {k: v for k, v in fm.items() if v is not None}
    return yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=1000).strip()


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_note(card: ReelCard) -> str:
    template_name = TEMPLATE_BY_TYPE.get(card.type, "fallback.md.j2")
    body = _env().get_template(template_name).render(card=card, a=card.analysis)
    return f"---\n{build_frontmatter(card)}\n---\n\n{body.strip()}\n"


def _note_shortcode(path: Path) -> str | None:
    """Точное значение поля shortcode из YAML-frontmatter заметки, иначе None.

    Сравнение по равенству (а не подстроке) — иначе короткий shortcode совпал бы
    с префиксом чужого и привёл к удалению чужой заметки. errors='ignore' —
    в vault может оказаться посторонний .md в иной кодировке.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]
    try:
        fm = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return None
    sc = fm.get("shortcode") if isinstance(fm, dict) else None
    return str(sc) if sc is not None else None


def note_path_for(card: ReelCard, settings: Settings) -> Path:
    reels_dir = settings.cfg.vault_path / "Reels"
    reels_dir.mkdir(parents=True, exist_ok=True)
    base = f"{card.saved:%Y-%m-%d}-{slugify(card.title)}"
    path = reels_dir / f"{base}.md"
    if path.exists():
        if _note_shortcode(path) == card.shortcode:
            return path  # переписываем свою же заметку (--force/retry)
        path = reels_dir / f"{base}-{card.shortcode}.md"  # коллизия имён — другой рилс
    return path


def write_note(card: ReelCard, settings: Settings) -> Path:
    path = note_path_for(card, settings)
    path.write_text(render_note(card), encoding="utf-8", newline="\n")
    # Подметаем старые файлы этого же рилса (заголовок мог измениться) — НО только если
    # новая карточка реально проанализирована: иначе неудачная переобработка (failed/unavailable)
    # затёрла бы хорошую карточку. Сравниваем shortcode по равенству, чтобы не задеть чужие.
    if card.analysis is None:
        return path
    reels_dir = settings.cfg.vault_path / "Reels"
    for other in reels_dir.glob("*.md"):
        if other == path:
            continue
        if _note_shortcode(other) == card.shortcode:
            try:
                other.unlink()
            except OSError:
                pass
    return path
