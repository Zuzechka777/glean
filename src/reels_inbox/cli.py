"""Точка входа: typer CLI.

Контракт для Зюзяна/внешних вызывателей:
  stdout при --json  → JSON-объект карточки
  stdout без --json  → одна строка вида «OK travel «...» → Reels/...»
  exit 0             → успех
  exit 2             → деградированный успех (карточка создана, но неполная)
  exit 1             → фатальная ошибка
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from .config import get_settings
from .ledger import Ledger
from .models import Status
from .pipeline import AlreadyProcessed, process
from .stages.download import AuthError
from .urls import find_instagram_urls
from .vault import init_vault

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # тише сторонние логгеры
    for noisy in ("httpx", "httpcore", "urllib3", "yt_dlp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ledger() -> Ledger:
    return Ledger(get_settings().ledger_path)


# ─── process ──────────────────────────────────────────────────────────────────

# имя задано явно: функция называется process_cmd (иначе конфликт с pipeline.process),
# а внешний контракт (Зюзян, доки) — «reels-inbox process»
@app.command(name="process")
def process_cmd(
    url: str = typer.Argument(..., help="Ссылка на Instagram Reel"),
    json_out: bool = typer.Option(False, "--json", help="Вернуть JSON карточки в stdout"),
    force: bool = typer.Option(False, "--force", help="Перезаписать уже обработанный рилс"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Обработать один рилс: скачать, проанализировать, сохранить в vault."""
    _setup_logging(verbose)
    settings = get_settings()
    ledger = _ledger()

    try:
        result = process(url, settings, ledger, force=force)
    except AlreadyProcessed as e:
        line = f"SKIP already_processed → {e.note_path}"
        if json_out:
            typer.echo(json.dumps({"status": "already_processed", "note_path": e.note_path}))
        else:
            typer.echo(line)
        raise SystemExit(0)
    except AuthError as e:
        typer.echo(f"ERROR auth: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        logging.getLogger(__name__).exception("fatal: %s", e)
        typer.echo(f"ERROR {e}", err=True)
        raise SystemExit(1)

    degraded = result.card.status in (Status.failed_download, Status.unavailable) or (
        result.card.note and "[caption-only" in result.card.note
    )
    if json_out:
        payload = {
            "status": result.card.status.value,
            "type": result.card.type.value,
            "title": result.card.title,
            "note_path": str(result.note_path),
            "cost_usd": round(result.cost_usd, 4),
            "degraded": degraded,
        }
        if result.card.analysis:
            payload["summary"] = result.card.analysis.summary
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(result.summary_line())

    raise SystemExit(2 if degraded else 0)


# ─── batch ────────────────────────────────────────────────────────────────────

@app.command()
def batch(
    file: Path = typer.Argument(..., help="saved_posts.json или txt со ссылками"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n"),
    force: bool = typer.Option(False, "--force"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Batch-обработка из файла IG-экспорта или txt-списка."""
    _setup_logging(verbose)
    from .batch import BatchReport, parse_batch_file, run_batch

    settings = get_settings()
    ledger = _ledger()

    urls = parse_batch_file(file)
    typer.echo(f"Найдено ссылок: {len(urls)}" + (f", обрабатываем {limit}" if limit else ""))

    report: BatchReport = run_batch(urls, settings, ledger, limit=limit, force=force)

    typer.echo(
        f"\n✓ Готово: {report.ok} ok / {report.degraded} degraded / "
        f"{report.failed} failed / {report.skipped} skip  |  "
        f"💰 ${report.cost_usd:.4f}"
    )
    if report.auth_stop:
        typer.echo("⚠️  Batch остановлен: ошибка аутентификации IG (cookies).", err=True)
        raise SystemExit(1)


# ─── retry ────────────────────────────────────────────────────────────────────

@app.command()
def retry(
    status: str = typer.Option("failed-download", help="failed-download | unavailable"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Повторить рилсы с заданным статусом."""
    _setup_logging(verbose)
    from .batch import run_batch

    settings = get_settings()
    ledger = _ledger()
    entries = ledger.pending([Status(status)], settings.cfg.max_attempts)
    typer.echo(f"Найдено на retry: {len(entries)}")

    urls = [e.url for e in entries]
    from .batch import BatchReport
    report: BatchReport = run_batch(urls, settings, ledger, limit=limit, force=True)
    typer.echo(
        f"Retry: {report.ok} ok / {report.degraded} degraded / "
        f"{report.failed} failed  |  ${report.cost_usd:.4f}"
    )


# ─── status ───────────────────────────────────────────────────────────────────

@app.command("status")
def status_cmd():
    """Показать сводку по ledger."""
    ledger = _ledger()
    s = ledger.stats()
    typer.echo(f"Всего в ledger: {s['total']}")
    for st, n in sorted(s["by_status"].items()):
        typer.echo(f"  {st}: {n}")
    typer.echo(f"Потрачено всего: ${s['total_cost_usd']:.4f}")


# ─── init-vault ───────────────────────────────────────────────────────────────

@app.command(name="init-vault")
def init_vault_cmd():
    """Создать структуру Obsidian vault и dashboard-заметки."""
    settings = get_settings()
    created = init_vault(settings.cfg.vault_path)
    if created:
        for p in created:
            typer.echo(f"  создан: {p}")
        typer.echo(f"Vault готов: {settings.cfg.vault_path}")
    else:
        typer.echo(f"Vault уже существует: {settings.cfg.vault_path}")


# ─── bot ──────────────────────────────────────────────────────────────────────

@app.command()
def bot(verbose: bool = typer.Option(False, "-v")):
    """Запустить fallback Telegram long-polling бот (whitelist по TELEGRAM_USER_ID)."""
    _setup_logging(verbose)
    from .telegram.bot import start_bot
    start_bot()


# ─── web ──────────────────────────────────────────────────────────────────────

@app.command()
def web(
    port: int = typer.Option(8765, "--port", "-p", help="Порт локального сервера"),
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="127.0.0.1 — только этот Mac; 0.0.0.0 — доступ с телефона по Wi-Fi (вся локалка)",
    ),
    verbose: bool = typer.Option(False, "-v"),
):
    """Запустить локальный веб-интерфейс (http://127.0.0.1:8765)."""
    _setup_logging(verbose)
    from .webui import start_web
    start_web(host=host, port=port)


# ─── (скрытый) process-text — для Зюзяна: извлечь IG-ссылку из текста ─────────

@app.command(name="process-text", hidden=True)
def process_text(
    text: str = typer.Argument(..., help="Произвольный текст с IG-ссылкой"),
    json_out: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Найти первую IG-ссылку в тексте и обработать её."""
    _setup_logging(verbose)
    urls = find_instagram_urls(text)
    if not urls:
        typer.echo("ERROR: IG-ссылка не найдена в тексте", err=True)
        raise SystemExit(1)
    # передаём управление основной команде
    sys.argv = ["reels-inbox", "process_cmd", urls[0]] + (["--json"] if json_out else [])
    process_cmd(url=urls[0], json_out=json_out, force=False, verbose=verbose)


# ─── entry point ──────────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()
