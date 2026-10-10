"""Контракт всего пайплайна: карточка, результат анализа, контекст прогона."""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class CardType(str, enum.Enum):
    travel = "travel"
    tutorial = "tutorial"
    offer = "offer"
    recipe = "recipe"
    ai_tools = "ai-tools"
    other = "other"


class Status(str, enum.Enum):
    inbox = "inbox"
    checked = "checked"
    done = "done"
    failed_download = "failed-download"
    unavailable = "unavailable"


class Place(BaseModel):
    """Место из travel-рилса. Координаты опциональны — Gemini знает не все."""

    name: str
    what: str = Field(description="Что это: кафе, смотровая, отель и т.п. и чем примечательно")
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

    @property
    def maps_url(self) -> str:
        if self.lat is not None and self.lng is not None:
            return f"https://www.google.com/maps/search/?api=1&query={self.lat},{self.lng}"
        query = f"{self.name} {self.city or ''}".strip().replace(" ", "+")
        return f"https://www.google.com/maps/search/?api=1&query={query}"


class OfferInfo(BaseModel):
    what: str = Field(description="Что предлагают, одной фразой")
    url: Optional[str] = None
    price: Optional[str] = None
    scam_verdict: str = Field(description="Похоже ли на скам и почему, 1-2 предложения")
    red_flags: list[str] = Field(default_factory=list)


class AiToolInfo(BaseModel):
    name: str
    what: str = Field(description="Что делает инструмент")
    url: Optional[str] = None
    try_in_5_min: str = Field(description="Как попробовать за 5 минут: конкретные шаги")


class AnalysisResult(BaseModel):
    """Структура, которую возвращает Gemini (response_schema) по видео + caption."""

    type: CardType
    title: str = Field(description="Короткий конкретный заголовок карточки на русском")
    summary: str = Field(description="Суть рилса в 2-4 предложениях: что предлагается сделать/узнать")
    key_points: list[str] = Field(
        default_factory=list,
        description="Главное по пунктам: конкретный список из контента (книги, промпты, "
        "советы, факты, шаги) — 3-10 коротких самодостаточных пунктов. Это основная "
        "выжимка, по которой владелец поймёт суть без просмотра. Каждый пункт — законченная мысль.",
    )
    transcript: Optional[str] = Field(default=None, description="Дословный транскрипт речи; null если речи нет")
    ocr_text: Optional[str] = Field(default=None, description="Текст с экрана (оверлеи, подписи), объединённый и без дублей")
    places: list[Place] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list, description="Шаги туториала/рецепта по порядку")
    ingredients: list[str] = Field(default_factory=list, description="Ингредиенты с количествами (для recipe)")
    offer: Optional[OfferInfo] = None
    ai_tool: Optional[AiToolInfo] = None
    tags: list[str] = Field(default_factory=list, description="3-6 тегов на русском, нижний регистр, без #")
    interests: list[str] = Field(
        default_factory=list,
        description="1-2 интереса владельца из фиксированного списка slug'ов (см. промпт); "
        "пусто, если ничего не подходит",
    )
    confidence: float = Field(ge=0, le=1, description="Уверенность в классификации и извлечении")


class ReelCard(BaseModel):
    """Готовая карточка: анализ + метаданные источника. Из неё рендерится заметка."""

    shortcode: str
    source: str
    status: Status
    analysis: Optional[AnalysisResult] = None
    caption: Optional[str] = None
    author: Optional[str] = None
    saved: datetime
    processed: datetime
    note: Optional[str] = None  # пояснение при деградации (что не получилось)

    @property
    def type(self) -> CardType:
        return self.analysis.type if self.analysis else CardType.other

    @property
    def title(self) -> str:
        if self.analysis:
            return self.analysis.title
        if self.caption:
            first = self.caption.strip().splitlines()[0]
            return first[:80]
        return f"Reel {self.shortcode}"


class PipelineContext(BaseModel):
    """Артефакты, накапливаемые по стадиям одного прогона."""

    model_config = {"arbitrary_types_allowed": True}

    url: str
    shortcode: str
    force: bool = False
    video_path: Optional[Path] = None
    image_paths: list[Path] = Field(default_factory=list)
    caption: Optional[str] = None
    author: Optional[str] = None
    duration_sec: Optional[float] = None
    downloader_used: Optional[str] = None
    analysis: Optional[AnalysisResult] = None
    analyzer_used: Optional[str] = None
    status: Status = Status.inbox
    degradation_note: Optional[str] = None
    cost_usd: float = 0.0
