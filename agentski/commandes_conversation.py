"""Couche Telegram de la conversation avec Krok : /talk, texte libre, /oublie, /achille."""
import asyncio
import logging
import random

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, amis, conversation, ia, reactions
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


def noter_dans_groupe(update: Update, texte: str) -> None:
    """Garde pour Krok un message du groupe de la série (jamais d'un autre groupe), à titre de contexte."""
    with SessionLocal() as session:
        if update.effective_chat.id != amis.groupe_id(session):
            return
        personne = acces.personne_connue(session, update.effective_user.id)
        if personne is not None:
            conversation.noter_ambiance(session, personne, "groupe", texte)


async def reponse_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un texte qui n'est ni une commande ni la réponse à une question en cours : Krok répond en privé, et dans le groupe seulement si on s'adresse à lui."""
    texte = update.message.text
    if update.effective_chat.type != "private":
        reponse_au_bot = bool(update.message.reply_to_message and update.message.reply_to_message.from_user
                              and update.message.reply_to_message.from_user.id == context.bot.id)
        adresse = conversation.adresse_a_krok(texte, context.bot.username, reponse_au_bot)
        if adresse is None:
            noter_dans_groupe(update, texte)  # il lit le groupe pour suivre la conversation, sans y répondre
            return
        texte = adresse
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


async def commande_krok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Coupe le mode Achille : Krok redevient Krok."""
    with SessionLocal() as session:
        actif = conversation.mode_achille(session, update.effective_chat.id)
        conversation.basculer_achille(session, update.effective_chat.id, False)
    await update.message.reply_text("Mode Achille coupé : je redeviens Krok, tout simplement." if actif else "Je suis déjà Krok, tout simplement.")


async def reagir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pose parfois une réaction-emoji sur un message du groupe (le groupe de la série uniquement)."""
    message = update.message
    if message is None or update.effective_chat.type == "private":
        return
    with SessionLocal() as session:
        if update.effective_chat.id != amis.groupe_id(session):
            return
        emoji = reactions.choisir(session, message.text or message.caption or "")
    if emoji:
        try:
            await message.set_reaction(emoji)
        except Exception:
            log.warning("Réaction %s refusée", emoji)


def installer(app) -> None:
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, reagir), group=7)  # groupe à part : n'empêche pas la réponse normale
    for nom, fonction in (("talk", commande_talk), ("oublie", commande_oublie), ("achille", commande_achille), ("krok", commande_krok)):
        app.add_handler(CommandHandler(nom, fonction))
