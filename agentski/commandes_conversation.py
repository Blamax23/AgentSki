"""Couche Telegram de la conversation avec Krok : /talk, texte libre, /oublie, /achille."""
import asyncio
import logging
import random

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from . import acces, conversation, ia
from .db import SessionLocal

log = logging.getLogger("agentski")


def _lieu(update: Update) -> str:
    return "prive" if update.effective_chat.type == "private" else "groupe"


async def converser(update: Update, context: ContextTypes.DEFAULT_TYPE, texte: str) -> None:
    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    def calcul() -> str:
        with SessionLocal() as session:
            personne = acces.personne_connue(session, update.effective_user.id)
            return conversation.repondre(session, personne, texte, _lieu(update), conversation.mode_achille(session, update.effective_chat.id),
                                         ia.conversation, ia.economique)
    try:
        reponse = await asyncio.to_thread(calcul)
    except Exception:
        log.exception("Conversation échouée")
        reponse = "J'ai eu un trou de mémoire, réessaie dans un instant."
    await update.message.reply_text(reponse)


async def reponse_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un texte qui n'est ni une commande ni la réponse à une question en cours : Krok répond en privé, et dans le groupe seulement si on s'adresse à lui."""
    texte = update.message.text
    if update.effective_chat.type != "private":
        reponse_au_bot = bool(update.message.reply_to_message and update.message.reply_to_message.from_user
                              and update.message.reply_to_message.from_user.id == context.bot.id)
        texte = conversation.adresse_a_krok(texte, context.bot.username, reponse_au_bot)
        if texte is None:
            return
    await converser(update, context, texte)


async def commande_talk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texte = " ".join(context.args).strip()
    if not texte:
        await update.message.reply_text("Dis-moi quelque chose : /talk ton message. En privé, tu peux aussi m'écrire directement.")
        return
    await converser(update, context, texte)


async def commande_oublie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(conversation.oublier(session, acces.personne_connue(session, update.effective_user.id)))


async def commande_achille(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voulu = {"on": True, "off": False}.get(context.args[0].lower()) if context.args else None
    with SessionLocal() as session:
        actif = conversation.basculer_achille(session, update.effective_chat.id, voulu)
    await update.message.reply_text(random.choice(conversation.ANNONCES_ACHILLE) if actif else "Mode Achille coupé : je redeviens Krok, tout simplement.")


def installer(app) -> None:
    for nom, fonction in (("talk", commande_talk), ("oublie", commande_oublie), ("achille", commande_achille)):
        app.add_handler(CommandHandler(nom, fonction))
