"""Couche Telegram des petites fonctions drôles : /preaud, /icham, /adrille_rallye, /duel, /quicestce."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import acces, amis, blagues, conversation, horoscope, ia
from .db import SessionLocal


def _clavier(boutons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in boutons]) if boutons else None


async def _repondre(update: Update, commande: str, texte: str, **options) -> None:
    """Envoie la réponse et la garde dans la mémoire de Krok : on peut ensuite lui en parler."""
    await update.message.reply_text(texte, **options)
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        if personne is not None:
            lieu = "prive" if update.effective_chat.type == "private" else "groupe"
            conversation.noter_commande(session, personne, lieu, commande, texte)


async def commande_preaud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _repondre(update, "preaud", blagues.PREAUD)


async def commande_icham(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _repondre(update, "icham", await asyncio.to_thread(blagues.traduire_arabe, " ".join(context.args), ia.economique))


async def commande_adrille_rallye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _repondre(update, "adrille_rallye", blagues.pub_adrille_rallye())


async def commande_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/horoscope : le tien ; /horoscope Adrien : celui de quelqu'un d'autre (prénom ou surnom)."""
    def calcul() -> str:
        with SessionLocal() as session:
            cible = amis.trouver_personne(session, " ".join(context.args)) if context.args else acces.personne_connue(session, update.effective_user.id)
            if cible is None:
                return "Je ne connais pas cette personne : /horoscope seul donne le tien, /horoscope Adrien celui d'un ami."
            return horoscope.horoscope(session, cible, ia.creatif)
    await _repondre(update, "horoscope", await asyncio.to_thread(calcul))


async def commande_duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def calcul() -> str:
        with SessionLocal() as session:
            return blagues.duel(session, context.args, ia.creatif)
    await _repondre(update, "duel", await asyncio.to_thread(calcul))


async def commande_quicestce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        resultat = blagues.nouvelle_devinette(session)
    if isinstance(resultat, str):
        await update.message.reply_text(resultat)
        return
    _, texte, boutons = resultat
    await _repondre(update, "quicestce", texte, reply_markup=_clavier(boutons))


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
                          ("adrille", commande_adrille_rallye), ("horoscope", commande_horoscope), ("duel", commande_duel), ("quicestce", commande_quicestce)):
        app.add_handler(CommandHandler(nom, fonction))
    app.add_handler(CallbackQueryHandler(bouton_quicestce, pattern=r"^q\|"))
