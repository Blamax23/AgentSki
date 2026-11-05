"""Couche Telegram du confessionnal : /confessionnal et les boutons du plan."""
import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import confessionnal
from .amis import aujourdhui
from .db import SessionLocal
from .models import Journee

log = logging.getLogger("agentski")


def _clavier(m) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in m.boutons])


async def commande_confessionnal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui(), Journee.ski.is_(True)))
        if journee is None:
            await update.message.reply_text("Pas de plan aujourd'hui, ce n'est pas un jour de ski déclaré.\n\n" + confessionnal.suivi(session))
            return
        confessionnal.proposer_plan(session, journee)
        m = confessionnal.message(session, journee)
        suivi = confessionnal.suivi(session)
    await update.message.reply_text(m.texte + "\n\n" + suivi, reply_markup=_clavier(m))


async def bouton_confessionnal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    requete = update.callback_query
    _, ligne_id, action = requete.data.split("|")
    with SessionLocal() as session:
        journee = confessionnal.decider(session, int(ligne_id), action)
        m = confessionnal.message(session, journee) if journee else None
    await requete.answer()
    if m is not None:
        try:
            await requete.edit_message_text(m.texte, reply_markup=_clavier(m))
        except Exception:
            log.warning("Plan du confessionnal inchangé")  # Telegram refuse de rééditer un message identique


def installer(app) -> None:
    app.add_handler(CommandHandler("confessionnal", commande_confessionnal))
    app.add_handler(CallbackQueryHandler(bouton_confessionnal, pattern=r"^c\|"))
