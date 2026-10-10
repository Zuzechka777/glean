"""Здоровье пайплайна: доступен ли Gemini (и через какой прокси), квота, cookies.

Проба Gemini = GET /models (не тратит квоту generate_content, но ловит
«location is not supported» — Google отвечает на неё так же, как на генерацию).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import Settings

log = logging.getLogger(__name__)

HAPP_PORTS = (10808, 10809, 10820)
_PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
_CACHE_TTL = 600  # сек: сколько помним выбранный маршрут

_lock = threading.Lock()
_route: dict = {"ts": 0.0, "proxy": None, "status": "unknown", "detail": ""}
_quota: dict = {"hit_at": None, "detail": ""}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def probe_gemini(api_key: str, proxy: Optional[str]) -> tuple[str, str]:
    """('ok'|'location'|'auth'|'error', детали)."""
    try:
        r = httpx.get(_PROBE_URL, headers={"x-goog-api-key": api_key}, proxy=proxy, timeout=10)
    except Exception as e:  # noqa: BLE001
        return "error", f"нет соединения: {type(e).__name__}"
    if r.status_code == 200:
        return "ok", ""
    text = r.text
    if "location is not supported" in text:
        return "location", "Google не принимает страну выхода"
    if r.status_code in (401, 403):
        return "auth", "ключ GEMINI_API_KEY отклонён"
    return "error", f"HTTP {r.status_code}"


def _candidates() -> list[Optional[str]]:
    explicit = os.environ.get("GEMINI_PROXY", "").strip()
    if explicit.lower() in ("off", "none", "0"):
        return [None]
    if explicit:
        return [explicit]
    ports = [f"http://127.0.0.1:{p}" for p in HAPP_PORTS if _port_open(p)]
    return ports + [None]  # None = напрямую, последним


def pick_gemini_route(settings: Settings, force: bool = False) -> dict:
    """Найти маршрут, через который Gemini отвечает. Кэш на _CACHE_TTL."""
    with _lock:
        if not force and time.time() - _route["ts"] < _CACHE_TTL:
            return dict(_route)
        key = settings.secrets.gemini_api_key or ""
        last = ("error", "ключ не задан") if not key else ("error", "нет маршрутов")
        chosen = None
        if key:
            for proxy in _candidates():
                st, detail = probe_gemini(key, proxy)
                last = (st, detail)
                if st == "ok":
                    chosen = proxy
                    break
        _route.update(ts=time.time(), proxy=chosen, status=last[0] if chosen is None else "ok",
                      detail="" if chosen is not None or last[0] == "ok" else last[1])
        if _route["status"] == "ok":
            log.info("gemini route: %s", chosen or "direct")
        else:
            log.warning("gemini route not found: %s %s", _route["status"], _route["detail"])
        return dict(_route)


def gemini_proxy(settings: Settings) -> Optional[str]:
    return pick_gemini_route(settings)["proxy"]


def invalidate_route() -> None:
    with _lock:
        _route["ts"] = 0.0


def note_quota_hit(detail: str) -> None:
    _quota.update(hit_at=time.time(), detail=detail[:200])


def note_analysis_ok() -> None:
    _quota.update(hit_at=None, detail="")


def cookies_have_sessionid(settings: Settings) -> bool:
    path = (settings.cfg.cookies_file or "").strip()
    if not path:
        return False
    p = Path(path).expanduser()
    try:
        return any(
            len(parts) >= 7 and parts[5] == "sessionid"
            for parts in (line.split("\t") for line in p.read_text(errors="ignore").splitlines())
        )
    except OSError:
        return False


def health(settings: Settings, force: bool = False) -> dict:
    r = pick_gemini_route(settings, force=force)
    gem_ok = r["status"] == "ok"
    quota_hit = _quota["hit_at"]
    via = (r["proxy"] or "напрямую") if gem_ok else None
    problems = []
    if not gem_ok:
        if r["status"] == "location":
            problems.append("Gemini не принимает страну выхода — переключи сервер в Happ (Германия работает)")
        elif r["status"] == "auth":
            problems.append("Ключ Gemini отклонён — проверь GEMINI_API_KEY")
        else:
            problems.append("Gemini недоступен — проверь, что Happ включён и подключён" if "соединения" in (r["detail"] or "") else f"Gemini недоступен: {r['detail'] or r['status']}")
    if quota_hit:
        problems.append("Кончилась дневная квота Gemini (free tier) — рилсы встанут в очередь и обработаются позже")
    return {
        "ok": gem_ok and not quota_hit,
        "gemini": {"status": r["status"], "via": via, "detail": r["detail"]},
        "quota_exhausted": bool(quota_hit),
        "cookies_sessionid": cookies_have_sessionid(settings),
        "problems": problems,
    }
