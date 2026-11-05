"""Couche Telegram de la position en direct : réception des positions, /position, /ou, /rdv."""
import asyncio

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, positions
from .db import SessionLocal

EXPLICATION = ("📍 Pour que /ou et /rdv fonctionnent, partage ta position en direct dans cette conversation : trombone, Position, "
               "« Partager ma position en direct », puis la durée.\n"
               "• N'active ça que pour les journées en groupe : le froid vide la batterie.\n"
               "• Je ne m'en sers que pour dire où vous êtes (secteur, piste), jamais pour des vitesses.\n"
               "• Les positions sont effacées une fois le week-end terminé.")


async def position_recue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Une position, neuve ou mise à jour d'un partage en direct (Telegram la renvoie comme message modifié)."""
    lieu = update.effective_message.location
    def garder() -> bool:
        with SessionLocal() as session:
            return positions.enregistrer_position(session, acces.personne_connue(session, update.effective_user.id), lieu.latitude, lieu.longitude,
                                                  getattr(lieu, "horizontal_accuracy", None))
    garde = await asyncio.to_thread(garder)
    if garde and update.edited_message is None:  # seulement au début d'un partage : pas un message à chaque mise à jour
        await update.effective_message.reply_text("📍 Position reçue : elle sert à /ou et /rdv, et elle est effacée après le week-end.")


async def commande_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(EXPLICATION)


async def commande_ou(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def calcul():
        with SessionLocal() as session:
            return positions.ou(session, " ".join(context.args) or None)
    texte, pins = await asyncio.to_thread(calcul)
    await update.message.reply_text(texte)
    for _, lat, lon in pins:
        await context.bot.send_location(update.effective_chat.id, lat, lon)


async def commande_rdv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def calcul():
        with SessionLocal() as session:
            return positions.rdv(session, acces.personne_connue(session, update.effective_user.id))
    texte, pin = await asyncio.to_thread(calcul)
    await update.message.reply_text(texte)
    if pin:
        await context.bot.send_location(update.effective_chat.id, *pin)


def installer(app) -> None:
    app.add_handler(MessageHandler(filters.LOCATION, position_recue))
    app.add_handler(CommandHandler("position", commande_position))
    app.add_handler(CommandHandler("ou", commande_ou))
    app.add_handler(CommandHandler("rdv", commande_rdv))
