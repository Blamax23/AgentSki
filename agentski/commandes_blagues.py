"""Couche Telegram des petites fonctions drôles : /preaud, /icham, /adrille_rallye, /duel, /quicestce."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import acces, blagues, ia
from .db import SessionLocal


def _clavier(boutons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in boutons]) if boutons else None


async def commande_preaud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(blagues.PREAUD)


async def commande_icham(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(await asyncio.to_thread(blagues.traduire_arabe, " ".join(context.args), ia.economique))


async def commande_adrille_rallye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(blagues.pub_adrille_rallye())


async def commande_duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def calcul() -> str:
        with SessionLocal() as session:
            return blagues.duel(session, context.args, ia.creatif)
    await update.message.reply_text(await asyncio.to_thread(calcul))


async def commande_quicestce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        resultat = blagues.nouvelle_devinette(session)
    if isinstance(resultat, str):
        await update.message.reply_text(resultat)
        return
    _, texte, boutons = resultat
    await update.message.reply_text(texte, reply_markup=_clavier(boutons))


async def bouton_quicestce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, devinette_id, choix = query.data.split("|")
    with SessionLocal() as session:
        resultat = blagues.repondre(session, int(devinette_id), acces.personne_connue(session, update.effective_user.id), choix)
    if resultat is None:
        await query.answer("Cette question est déjà close.", show_alert=True)
        return
    await query.answer()
    texte, boutons = resultat
    await query.edit_message_text(texte, reply_markup=_clavier(boutons))


def installer(app) -> None:
    for nom, fonction in (("preaud", commande_preaud), ("icham", commande_icham), ("adrille_rallye", commande_adrille_rallye),
                          ("adrille", commande_adrille_rallye), ("duel", commande_duel), ("quicestce", commande_quicestce)):
        app.add_handler(CommandHandler(nom, fonction))
    app.add_handler(CallbackQueryHandler(bouton_quicestce, pattern=r"^q\|"))
