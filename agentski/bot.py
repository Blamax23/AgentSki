import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters
from telegram.ext import ApplicationHandlerStop

from . import config
from .db import SessionLocal, sync_allowed_users

log = logging.getLogger("agentski")


async def liste_blanche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Le bot ignore les inconnus ; leur identifiant est noté dans les logs pour l'ajouter à ALLOWED_USERS."""
    user = update.effective_user
    if user is None or user.id not in config.allowed_users():
        if user is not None:
            log.warning("Inconnu ignoré : id=%s prénom=%s", user.id, user.first_name)
        raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prenom = config.allowed_users()[update.effective_user.id]
    await update.message.reply_text(f"Salut {prenom}, je suis là.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Reçu. (Le socle répond, le reste arrive aux étapes suivantes.)")


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # ses logs contiennent le token dans l'URL
    with SessionLocal() as session:
        sync_allowed_users(session)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(TypeHandler(Update, liste_blanche), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, echo))
    app.run_polling()
