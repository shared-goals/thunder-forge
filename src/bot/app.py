from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from services.access_service import is_admin_telegram_id
from services.config_service import load_config


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    if not is_admin_telegram_id(user.id):
        await update.effective_message.reply_text("Forbidden")
        return

    # Localhost dev is expected. For real Telegram Mini App usage you'll need a reachable URL.
    mini_app_url = load_config().mini_app_url
    await update.effective_message.reply_text(
        "Thunder Forge is running. Open the Mini App at:\n" + mini_app_url
    )


async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    if not is_admin_telegram_id(user.id):
        await update.effective_message.reply_text("Forbidden")
        return

    await update.effective_message.reply_text(
        "/start - show Mini App URL\n"
        "/help - show this help\n"
    )


def create_bot_app() -> Application:
    token = load_config().telegram.bot_token

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("help", _help))
    return app
