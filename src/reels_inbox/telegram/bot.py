"""Fallback Telegram long-polling бот.

Работает без webhook → нет нужды в публичном IP/порте, работает за NAT.
Принимает IG-ссылки от whitelisted user, запускает pipeline, отвечает резюме.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _format_reply(payload: dict) -> str:
    status = payload.get("status", "?")
    title = payload.get("title", "—")
    card_type = payload.get("type", "?")
    summary = payload.get("summary", "")
    cost = payload.get("cost_usd", 0)
    degraded = payload.get("degraded", False)

    icon = {
        "travel": "🗺️",
        "tutorial": "📋",
        "offer": "🛒",
        "recipe": "🍽️",
        "ai-tools": "🤖",
        "other": "📎",
    }.get(card_type, "📎")

    warning = "\n⚠️ Обработан частично (деградация)" if degraded else ""

    return (
        f"{icon} *{title}*\n"
        f"Тип: {card_type} | статус: {status}\n\n"
        f"{summary}{warning}\n\n"
        f"💰 ${cost:.4f}"
    )


async def _handle_message(update, context) -> None:
    from ..config import get_settings
    from ..ledger import Ledger
    from ..pipeline import AlreadyProcessed, process
    from ..urls import find_instagram_urls

    settings = get_settings()
    allowed_id = settings.secrets.telegram_user_id
    if allowed_id and update.effective_user and update.effective_user.id != allowed_id:
        return  # игнорируем чужих

    text = (update.message.text or "") + " " + (update.message.caption or "")
    urls = find_instagram_urls(text)
    if not urls:
        return

    for url in urls:
        await update.message.reply_text(f"⏳ Обрабатываю: {url}")
        ledger = Ledger(settings.ledger_path)
        try:
            result = process(url, settings, ledger, force=False)
            payload = {
                "status": result.card.status.value,
                "type": result.card.type.value,
                "title": result.card.title,
                "summary": result.card.analysis.summary if result.card.analysis else "",
                "cost_usd": round(result.cost_usd, 4),
                "degraded": result.card.status.value in ("failed-download", "unavailable"),
            }
        except AlreadyProcessed as e:
            await update.message.reply_text(f"✅ Уже обработан:\n`{e.note_path}`", parse_mode="Markdown")
            continue
        except Exception as e:
            log.exception("pipeline error for %s", url)
            await update.message.reply_text(f"❌ Ошибка: {e}")
            continue
        finally:
            ledger.close()

        await update.message.reply_text(
            _format_reply(payload),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )


def start_bot() -> None:
    from telegram.ext import ApplicationBuilder, MessageHandler, filters

    from ..config import get_settings

    settings = get_settings()
    token = settings.secrets.telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, _handle_message))

    log.info("Starting Telegram bot (long-polling)...")
    app.run_polling(allowed_updates=["message"])
