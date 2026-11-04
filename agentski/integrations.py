"""Couche Telegram des connexions : /son et liens Spotify, /ics, réponse d'Alix."""
import asyncio
import io

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, agenda, config, spotify
from .amis import definir_reglage
from .db import SessionLocal
from .models import Personne
from sqlalchemy import select


def _ajouter(texte: str, telegram_id: int) -> str:
    with SessionLocal() as session:
        return spotify.ajouter_morceau(session, acces.personne_connue(session, telegram_id), texte)


async def commande_son(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reponse = await asyncio.to_thread(_ajouter, " ".join(context.args), update.effective_user.id)
    await update.message.reply_text(reponse)


async def lien_spotify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un lien Spotify collé dans le groupe est ajouté à la playlist, sans commande."""
    if not spotify.configure():
        return  # pas de réponse dans le groupe tant que Spotify n'est pas branché
    reponse = await asyncio.to_thread(_ajouter, update.message.text, update.effective_user.id)
    await update.message.reply_text(reponse)


async def commande_ics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        contenu = agenda.exporter_ics(session)
    if contenu is None:
        await update.message.reply_text("Aucune journée de ski déclarée pour l'instant.")
        return
    await update.message.reply_document(io.BytesIO(contenu), filename="chronique-ski.ics",
                                        caption="À importer dans ton agenda. (Un abonnement automatique demanderait un port ouvert sur le serveur.)")


async def bouton_alix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, vendredi, code = query.data.split("|")
    reponse = agenda.OPTIONS_ALIX[int(code)]
    with SessionLocal() as session:
        definir_reglage(session, f"alix:{vendredi}", reponse)
        orga = session.scalar(select(Personne).where(Personne.prenom == config.ORGANISATEUR))
        destinataire = orga.telegram_id if orga else None
    await query.answer()
    await query.edit_message_text(f"Merci Alix ! Noté : {reponse}.")
    if destinataire:
        await context.bot.send_message(destinataire, f"🔔 Alix répond pour le week-end du {vendredi[8:]}/{vendredi[5:7]} : {reponse}.")


def installer(app) -> None:
    app.add_handler(CommandHandler("son", commande_son))
    app.add_handler(CommandHandler("ics", commande_ics))
    app.add_handler(CallbackQueryHandler(bouton_alix, pattern=r"^al\|"))
    app.add_handler(MessageHandler(filters.Regex(spotify.LIEN) & ~filters.COMMAND, lien_spotify))
