"""Локальный веб-интерфейс Glean.

Одностраничный UI (static/index.html) + тонкий FastAPI поверх pipeline.process.
Запуск: reels-inbox web  (или двойной клик по START_HERE.command).

Маршруты:
- GET  /                 — страница
- POST /api/process      — поставить рилс в очередь {url} → {job_id} (мгновенно)
- GET  /api/job/{id}     — статус job: queued (+position) | running | done (+result)
- GET  /api/recent       — последние карточки из vault (для списка справа)
- GET  /api/stats        — сводка ledger (всего/потрачено)

Обработка асинхронная: пайплайн (скачивание + Gemini) идёт 1–5 минут, держать
столько один HTTP-запрос нельзя — браузеры/прокси/VPN рвут долгие соединения
(«Failed to fetch»), хотя сервер доделывает. Поэтому POST лишь ставит job,
а фронт поллит короткими GET.
"""

from __future__ import annotations

import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import get_settings
from .ledger import Ledger
from .models import Status
from .pipeline import AlreadyProcessed, Deferred, process
from . import health as _health, retry_queue
from .urls import find_instagram_urls

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Glean")

# pipeline.process — блокирующий (сеть + Gemini), гоним в отдельном потоке,
# чтобы event loop FastAPI не вис. По одному за раз (max_workers=1) — бережём IG.
_executor = ThreadPoolExecutor(max_workers=1)

# jobs живут в памяти процесса: перезапуск сервера теряет статусы (но не карточки —
# они уже в vault). insertion order dict = порядок очереди.
_jobs: dict[str, dict] = {}
_JOBS_KEEP = 100  # финишировавших джобов держим не больше этого


class ProcessReq(BaseModel):
    url: str
    force: bool = False


def _obsidian_url(note_path: str | Path) -> str | None:
    """Построить obsidian://open ссылку: ищем ближайший корень vault (.obsidian) среди предков."""
    p = Path(note_path).expanduser().resolve()
    for d in p.parents:
        if (d / ".obsidian").is_dir():
            rel = p.relative_to(d)
            return f"obsidian://open?vault={quote(d.name)}&file={quote(str(rel))}"
    return None


def _process_blocking(url: str, force: bool) -> dict:
    settings = get_settings()
    ledger = Ledger(settings.ledger_path)
    try:
        urls = find_instagram_urls(url) or [url.strip()]
        target = urls[0]
        try:
            result = process(target, settings, ledger, force=force)
        except AlreadyProcessed as e:
            return {"status": "already_processed", "note_path": str(e.note_path),
                    "obsidian": _obsidian_url(e.note_path), "url": target}
        except Deferred as e:
            return {"status": "deferred", "url": target,
                    "error": "Gemini сейчас недоступен — рилс отложен и обработается автоматически, "
                             "как только Gemini снова ответит", "reason": e.reason[:200]}

        card = result.card
        a = card.analysis
        degraded = card.status in (Status.failed_download, Status.unavailable) or (
            card.note and "[caption-only" in card.note
        )
        # если анализа нет (скачать/разобрать не вышло) — показать причину вместо пустоты
        summary = a.summary if a else (card.note or "Не удалось обработать рилс").strip()
        return {
            "status": card.status.value,
            "type": card.type.value,
            "title": card.title,
            "summary": summary,
            "tags": a.tags if a else [],
            "interests": a.interests if a else [],
            "author": card.author,
            "source": card.source,
            "note_path": str(result.note_path),
            "obsidian": _obsidian_url(result.note_path),
            "saved": card.saved.strftime("%Y-%m-%d"),
            "cost_usd": round(result.cost_usd, 4),
            "degraded": bool(degraded),
            "url": target,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("webui process error")
        return {"status": "error", "error": str(e), "url": url}
    finally:
        ledger.close()


def _run_job(job_id: str, url: str, force: bool) -> dict:
    job = _jobs.get(job_id)
    if job is not None:
        job["state"] = "running"
    try:
        payload = _process_blocking(url, force)
    except Exception as e:  # noqa: BLE001 — job не должен зависнуть «running» навечно
        log.exception("job %s crashed", job_id)
        payload = {"status": "error", "error": str(e), "url": url}
    if job is not None:
        job["state"] = "done"
        job["result"] = payload
    return payload


def _prune_jobs() -> None:
    done = [jid for jid, j in _jobs.items() if j["state"] == "done"]
    for jid in done[:-_JOBS_KEEP]:
        _jobs.pop(jid, None)


@app.post("/api/process")
async def api_process(req: ProcessReq):
    _prune_jobs()
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"state": "queued", "url": req.url, "result": None}
    _executor.submit(_run_job, job_id, req.url, req.force)
    return JSONResponse({"job_id": job_id, "status": "queued", "url": req.url})


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(
            {"status": "error",
             "error": "job не найден (сервер перезапускался?) — проверь коллекцию, карточка могла успеть создаться"},
            status_code=404,
        )
    if job["state"] == "done":
        return JSONResponse({"status": "done", "result": job["result"]})
    if job["state"] == "running":
        return JSONResponse({"status": "running"})
    # позиция в очереди — сколько незавершённых джобов встало раньше этого
    position = 0
    for jid, j in _jobs.items():
        if jid == job_id:
            break
        if j["state"] in ("queued", "running"):
            position += 1
    return JSONResponse({"status": "queued", "position": position})


def _parse_frontmatter(text: str) -> dict:
    """Разобрать YAML-frontmatter заметки (между первыми ---). {} при любой проблеме."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]
    try:
        fm = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def _read_recent(limit: int = 100) -> list[dict]:
    settings = get_settings()
    reels_dir = settings.cfg.vault_path / "Reels"
    if not reels_dir.exists():
        return []
    # (path, mtime), пропуская исчезнувшие между glob и stat (гонка с /api/process)
    entries = []
    for p in reels_dir.glob("*.md"):
        try:
            entries.append((p, p.stat().st_mtime))
        except OSError:
            continue
    entries.sort(key=lambda t: t[1], reverse=True)

    out: list[dict] = []
    seen: set[str] = set()
    for f, _ in entries:
        try:
            fm = _parse_frontmatter(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue  # файл исчез между glob и read
        sc = str(fm.get("shortcode") or f.stem)
        if sc in seen:  # дедуп по рилсу (orphan-дубли на диске не множим в UI)
            continue
        seen.add(sc)
        out.append({
            "title": str(fm.get("title") or f.stem),
            "type": str(fm.get("type") or "other"),
            "source": str(fm.get("source") or ""),
            "saved": str(fm.get("saved") or ""),
            "file": f.name,
            "obsidian": _obsidian_url(f),
            "interests": fm.get("interests") if isinstance(fm.get("interests"), list) else [],
            "used": bool(fm.get("used", False)),
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda r: r["used"])  # непросмотренное/неиспользованное — первым (stable: внутри групп свежее выше)
    return out


@app.get("/api/recent")
def api_recent():
    return JSONResponse(_read_recent())


@app.get("/api/note")
def api_note(file: str):
    """Содержимое одной заметки для встроенного ридера. Только файлы из Reels-папки."""
    settings = get_settings()
    reels_dir = settings.cfg.vault_path / "Reels"
    p = reels_dir / Path(file).name  # basename — защита от path traversal
    if p.suffix != ".md" or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return JSONResponse({"error": "unreadable"}, status_code=500)
    fm = _parse_frontmatter(text)
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            body = text[end + 4:]
    return JSONResponse({
        "title": str(fm.get("title") or p.stem),
        "type": str(fm.get("type") or "other"),
        "author": str(fm.get("author") or ""),
        "source": str(fm.get("source") or ""),
        "tags": fm.get("tags") if isinstance(fm.get("tags"), list) else [],
        "obsidian": _obsidian_url(p),
        "markdown": body.strip(),
    })


class UsedReq(BaseModel):
    file: str
    used: bool


@app.post("/api/used")
def api_used(req: UsedReq):
    """Пометить карточку «использовано/не использовано»: правим поле used в frontmatter.

    Точечная замена строки (а не пере-дамп YAML) — чтобы не переформатировать заметку.
    """
    settings = get_settings()
    p = settings.cfg.vault_path / "Reels" / Path(req.file).name  # basename — от traversal
    if p.suffix != ".md" or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return JSONResponse({"error": "no frontmatter"}, status_code=422)
    end = text.find("\n---", 3)
    if end == -1:
        return JSONResponse({"error": "no frontmatter"}, status_code=422)
    fm_block, rest = text[:end], text[end:]
    val = "true" if req.used else "false"
    new_fm, n = re.subn(r"(?m)^used:\s*\S+\s*$", f"used: {val}", fm_block)
    if n == 0:
        new_fm = fm_block.rstrip("\n") + f"\nused: {val}"
    p.write_text(new_fm + rest, encoding="utf-8", newline="\n")
    return JSONResponse({"ok": True, "file": p.name, "used": req.used})


_PLACE_HDR = re.compile(r"^### \d+\.\s+(.+?)\s*$", re.M)
_PLACE_GEO = re.compile(r"\[geo:([\d.\-]+),([\d.\-]+)\]")
_PLACE_CITY = re.compile(r"^\*\*.+?\*\*\s*·\s*(.+?)\s*$", re.M)


def _geocode(query: str, cache: dict) -> tuple[float, float] | None:
    """Nominatim (OSM) c файловым кэшем; вежливая пауза между промахами."""
    import time

    import httpx

    if query in cache:
        v = cache[query]
        return tuple(v) if v else None
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "glean-personal-tool/1.0"},
            timeout=10,
        )
        hits = r.json()
        coords = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
    except Exception:  # noqa: BLE001 — карта не должна ронять API
        return None  # не кэшируем сетевые сбои
    cache[query] = list(coords) if coords else None
    time.sleep(1.1)  # правила Nominatim: ≤1 rps
    return coords


@app.get("/api/places")
def api_places():
    """Все места из travel-карточек c координатами (geo: из заметки или Nominatim)."""
    import json as _json

    settings = get_settings()
    reels_dir = settings.cfg.vault_path / "Reels"
    cache_path = settings.cfg.data_dir / "geocache.json"
    try:
        cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cache = {}
    out = []
    for f in sorted(reels_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if str(fm.get("type")) != "travel":
            continue
        title = str(fm.get("title") or f.stem)
        # блоки мест: заголовок "### N. Имя", в блоке — city и (опц.) geo:
        headers = list(_PLACE_HDR.finditer(text))
        for i, m in enumerate(headers):
            block = text[m.end(): headers[i + 1].start() if i + 1 < len(headers) else len(text)]
            name = m.group(1)
            geo = _PLACE_GEO.search(block)
            city_m = _PLACE_CITY.search(block)
            city = city_m.group(1) if city_m else ""
            if geo:
                lat, lon = float(geo.group(1)), float(geo.group(2))
            else:
                q = f"{name}, {city}" if city and city.lower() != name.lower() else name
                coords = _geocode(q, cache)
                if not coords:
                    continue
                lat, lon = coords
            out.append({"name": name, "city": city, "lat": lat, "lon": lon,
                        "file": f.name, "title": title})
    try:
        cache_path.write_text(_json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    except OSError:
        pass
    return JSONResponse(out)


@app.get("/api/health")
def api_health(force: bool = False):
    h = _health.health(get_settings(), force=force)
    h["queue"] = len(retry_queue.load(get_settings()))
    return JSONResponse(h)


def _retry_worker(interval: int = 900) -> None:
    """Раз в 15 мин: если очередь не пуста и Gemini отвечает — догоняем отложенные."""
    import time

    while True:
        time.sleep(interval)
        try:
            settings = get_settings()
            items = retry_queue.load(settings)
            if not items:
                continue
            h = _health.health(settings, force=True)
            if not h["ok"]:
                log.info("retry worker: Gemini ещё недоступен (%s), очередь %d", h["problems"], len(items))
                continue
            for it in items:
                payload = _executor.submit(_process_blocking, it["url"], True).result()
                log.info("retry worker: %s → %s", it["shortcode"], payload.get("status"))
                if payload.get("status") == "deferred":
                    break  # снова упёрлись — ждём следующего круга
        except Exception:  # noqa: BLE001
            log.exception("retry worker error")


@app.get("/api/stats")
def api_stats():
    settings = get_settings()
    ledger = Ledger(settings.ledger_path)
    try:
        st = ledger.stats()
    finally:
        ledger.close()
    return JSONResponse(st)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _lan_ip() -> str | None:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def start_web(host: str = "127.0.0.1", port: int = 8765) -> None:
    import threading

    threading.Thread(target=_retry_worker, daemon=True, name="retry-worker").start()
    import uvicorn

    print(f"  💻 этот Mac:  http://127.0.0.1:{port}")
    if host == "0.0.0.0":  # noqa: S104  (осознанно — доступ с телефона по Wi-Fi)
        ip = _lan_ip()
        if ip:
            print(f"  📱 с телефона: http://{ip}:{port}   (Mac и телефон в одной Wi-Fi)")
        print("  ⚠️  открыт для всей локальной сети (без пароля) — только в доверенной Wi-Fi")
    uvicorn.run(app, host=host, port=port, log_level="warning")
