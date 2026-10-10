"""Анализ рилса. Основная стратегия — один вызов Gemini Flash с видеофайлом:
транскрипт + OCR оверлеев + места + классификация + карточка одним response_schema.

Запасные:
- whisper-gemini: транскрипт делает Groq Whisper (эталон русского ASR),
  Gemini получает видео + готовый транскрипт;
- caption-only: видео добыть не удалось — классификация по caption текстом.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ..config import Settings
from ..models import AnalysisResult

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "analyze_video.md"


class AnalysisFailed(Exception):
    pass


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _gemini_client(settings: Settings):
    # REELS_PROXY должен действовать и на Gemini (VPN не всегда включён):
    # google-genai использует httpx, который читает HTTPS_PROXY из окружения.
    import os

    if settings.proxy:
        os.environ.setdefault("HTTPS_PROXY", settings.proxy)
        os.environ.setdefault("HTTP_PROXY", settings.proxy)

    from google import genai
    from google.genai import types

    if not settings.secrets.gemini_api_key:
        raise AnalysisFailed("GEMINI_API_KEY is not set")
    gproxy = _gemini_proxy(settings)
    if gproxy:
        log.info("gemini via proxy %s", gproxy)
        opts = types.HttpOptions(client_args={"proxy": gproxy}, async_client_args={"proxy": gproxy})
        return genai.Client(api_key=settings.secrets.gemini_api_key, http_options=opts)
    return genai.Client(api_key=settings.secrets.gemini_api_key)


def _port_open(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _gemini_proxy(settings: Settings) -> Optional[str]:
    """Прокси ТОЛЬКО для Gemini (скачивание из IG идёт своим путём).

    Зачем: правила маршрутизации VPN-клиента (Happ) пускают googleapis.com
    напрямую с российского IP → Gemini отвечает «User location is not supported».
    Через локальный прокси Happ запрос уходит на VPN-сервер.
    Порядок: GEMINI_PROXY из .env/окружения ('off' — отключить) → автодетект
    локального порта Happ 127.0.0.1:10808 → без прокси.
    """
    import os

    explicit = os.environ.get("GEMINI_PROXY", "").strip()
    if explicit.lower() in ("off", "none", "0"):
        return None
    if explicit:
        return explicit
    if _port_open("127.0.0.1", 10808):
        return "http://127.0.0.1:10808"
    return None


def _estimate_cost(settings: Settings, usage) -> float:
    if usage is None:
        return 0.0
    # gemini-2.5-flash — thinking-модель: токены мышления биллятся по output-цене,
    # но лежат в отдельном поле thoughts_token_count (иначе расход недоучитывается).
    out_tokens = (usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
    inp = (usage.prompt_token_count or 0) / 1_000_000 * settings.cfg.gemini_price_per_1m_input
    out = out_tokens / 1_000_000 * settings.cfg.gemini_price_per_1m_output
    return round(inp + out, 6)


def is_hard_gemini_error(msg: str) -> bool:
    """Ошибки, которые не лечатся повтором/фолбэком в ту же минуту:
    исчерпана дневная квота free tier или Google не принимает страну выхода."""
    m = msg or ""
    return ("PerDay" in m or "free_tier" in m) and "RESOURCE_EXHAUSTED" in m or \
        "location is not supported" in m


def _is_transient(exc: BaseException) -> bool:
    """Ретраить только временное: 429/5xx/таймауты. Не тратить ретраи на 4xx/валидацию."""
    import httpx

    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 and is_hard_gemini_error(str(exc)):
        return False  # дневная квота free tier: ретраи только сжигают остаток квоты
    if code in (408, 429, 500, 502, 503, 504):
        return True
    return type(exc).__name__ == "ServerError"


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=5, max=60),
    reraise=True,
)
def _generate(client, model: str, contents: list, settings: Settings, schema) -> object:
    from google.genai import types

    cfg = dict(
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
        max_output_tokens=settings.cfg.gemini_max_output_tokens,
    )
    # ограничиваем «размышления», чтобы они не съедали бюджет вывода (обрыв JSON)
    budget = settings.cfg.gemini_thinking_budget
    if budget is not None:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
    resp = client.models.generate_content(
        model=model, contents=contents, config=types.GenerateContentConfig(**cfg)
    )
    # явная диагностика обрыва: даём шанс на ретрай вместо невалидного JSON
    cand = (resp.candidates or [None])[0]
    finish = getattr(getattr(cand, "finish_reason", None), "name", "")
    if finish == "MAX_TOKENS":
        raise AnalysisFailed(
            f"Gemini оборвал ответ по лимиту токенов (finish=MAX_TOKENS); "
            f"увеличь gemini_max_output_tokens (сейчас {settings.cfg.gemini_max_output_tokens})"
        )
    return resp


def _upload_video(client, video_path: Path):
    """Files API: загрузить и дождаться состояния ACTIVE."""
    f = client.files.upload(file=str(video_path))
    deadline = time.monotonic() + 300
    while getattr(f.state, "name", str(f.state)) == "PROCESSING":
        if time.monotonic() > deadline:
            raise AnalysisFailed(f"Gemini file {f.name} stuck in PROCESSING")
        time.sleep(3)
        f = client.files.get(name=f.name)
    state = getattr(f.state, "name", str(f.state))
    if state != "ACTIVE":
        raise AnalysisFailed(f"Gemini file {f.name} state={state}")
    return f


def _parse_result(resp) -> AnalysisResult:
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, AnalysisResult):
        return parsed
    # запасной путь: парсим JSON-текст сами
    return AnalysisResult.model_validate_json(resp.text)


class GeminiVideoAnalyzer:
    name = "gemini-video"

    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze(
        self, video_path: Path, caption: Optional[str], transcript_hint: Optional[str] = None
    ) -> tuple[AnalysisResult, float]:
        client = _gemini_client(self.settings)
        f = _upload_video(client, video_path)
        try:
            prompt = _load_prompt()
            parts = [f, prompt, f"\nCaption автора:\n{caption or '(нет)'}"]
            if transcript_hint:
                parts.append(
                    "\nГотовый транскрипт речи (сделан Whisper, используй его "
                    f"как основной, по видео добери остальное):\n{transcript_hint}"
                )
            resp = _generate(client, self.settings.cfg.gemini_model, parts, self.settings, AnalysisResult)
            return _parse_result(resp), _estimate_cost(self.settings, resp.usage_metadata)
        finally:
            try:
                client.files.delete(name=f.name)
            except Exception:
                log.debug("could not delete remote file %s", f.name, exc_info=True)


class GeminiImagesAnalyzer:
    """Карусель/фото-пост: отправляем все слайды картинками в один запрос Gemini.

    Аудио/видео нет — transcript оставляем null, текст со слайдов идёт в ocr_text.
    """

    name = "gemini-images"

    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze(self, image_paths: list[Path], caption: Optional[str]) -> tuple[AnalysisResult, float]:
        from google.genai import types

        if not image_paths:
            raise AnalysisFailed("GeminiImagesAnalyzer: пустой список слайдов")
        client = _gemini_client(self.settings)
        n = len(image_paths)
        prompt = (
            _load_prompt()
            + f"\n\nЭТО НЕ ВИДЕО, А КАРУСЕЛЬ ИЗ {n} СЛАЙДОВ (изображения по порядку). "
            "Звука нет: transcript оставь null. Весь текст, прочитанный со слайдов, "
            "собери по порядку в ocr_text. Разбирай слайды как единое целое — "
            "часто это лонгрид/гайд/список, разбитый по картинкам."
        )
        parts: list = [prompt, f"\nCaption автора:\n{caption or '(нет)'}"]
        for i, p in enumerate(image_paths):
            parts.append(f"\n— слайд {i + 1} —")
            parts.append(types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg"))
        resp = _generate(client, self.settings.cfg.gemini_model, parts, self.settings, AnalysisResult)
        result = _parse_result(resp)
        result.transcript = None
        return result, _estimate_cost(self.settings, resp.usage_metadata)


class WhisperGeminiAnalyzer:
    """Groq Whisper для русского транскрипта + Gemini по видео для остального."""

    name = "whisper-gemini"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _extract_audio(self, video_path: Path) -> Path:
        out = Path(tempfile.mkstemp(suffix=".m4a")[1])
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AnalysisFailed(f"ffmpeg audio extraction failed: {proc.stderr[-500:]}")
        return out

    def _transcribe(self, audio_path: Path) -> Optional[str]:
        from groq import Groq

        if not self.settings.secrets.groq_api_key:
            raise AnalysisFailed("GROQ_API_KEY is not set for whisper-gemini analyzer")
        client = Groq(api_key=self.settings.secrets.groq_api_key)
        with open(audio_path, "rb") as fh:
            result = client.audio.transcriptions.create(
                file=(audio_path.name, fh),
                model=self.settings.cfg.groq_whisper_model,
                response_format="text",
            )
        text = result if isinstance(result, str) else getattr(result, "text", None)
        return text.strip() if text and text.strip() else None

    def analyze(self, video_path: Path, caption: Optional[str]) -> tuple[AnalysisResult, float]:
        audio = self._extract_audio(video_path)
        try:
            transcript = self._transcribe(audio)
        finally:
            audio.unlink(missing_ok=True)
        result, cost = GeminiVideoAnalyzer(self.settings).analyze(
            video_path, caption, transcript_hint=transcript
        )
        if transcript:
            result.transcript = transcript
        return result, cost


class CaptionOnlyAnalyzer:
    """Деградация: видео не добыто, классифицируем по caption текстовой моделью."""

    name = "caption-only"

    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze(self, caption: str, url: str) -> tuple[AnalysisResult, float]:
        client = _gemini_client(self.settings)
        prompt = (
            _load_prompt()
            + "\n\nВИДЕО НЕДОСТУПНО. У тебя есть только caption и ссылка. "
            "Заполни что можешь, transcript и ocr_text оставь null, "
            "confidence не выше 0.5.\n"
            f"Ссылка: {url}\nCaption:\n{caption}"
        )
        resp = _generate(client, self.settings.cfg.gemini_model, [prompt], self.settings, AnalysisResult)
        return _parse_result(resp), _estimate_cost(self.settings, resp.usage_metadata)
