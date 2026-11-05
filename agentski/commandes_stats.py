"""Couche Telegram des statistiques : /stats, /chrono, /fils, /afaire, /gopro, /kit, /episode, /vues."""
import asyncio
import io
from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from . import acces, amis, stats, youtube
from .db import SessionLocal


def _moi(session, update: Update):
    return acces.personne_connue(session, update.effective_user.id)


async def commande_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        demande = " ".join(context.args).strip()
        if demande.lower() in ("tous", "groupe"):
            await update.message.reply_text(stats.stats_tous(session))
            return
        personne = amis.personne_par_prenom(session, demande) if demande else _moi(session, update)
        if personne is None:
            await update.message.reply_text(f"Je ne connais pas « {demande} ». Essaie un prénom, ou « tous ».")
            return
        await update.message.reply_text(stats.stats_personne(session, personne))


async def commande_chrono(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(stats.ajouter_chrono(session, _moi(session, update), context.args))


async def commande_fils(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(stats.fils(session))


async def commande_afaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(stats.afaire(session))


async def commande_gopro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(stats.gopro(session))


async def commande_kit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        cible = datetime.strptime(context.args[0], "%Y-%m-%d").date() if context.args else None
    except ValueError:
        await update.message.reply_text("Format : /kit, ou /kit 2026-12-12 pour un autre week-end")
        return
    with SessionLocal() as session:
        resultat = stats.kit(session, cible)
    if resultat is None:
        await update.message.reply_text("Aucun week-end avec une journée de ski à exporter.")
        return
    nom, contenu = resultat
    await update.message.reply_document(io.BytesIO(contenu.encode("utf-8")), filename=nom,
                                        caption="Ce qui a été vécu, en vrac : le montage reste à toi.")


async def commande_episode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(youtube.enregistrer_episode(session, context.args))


async def commande_vues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def calcul() -> str:
        with SessionLocal() as session:
            return youtube.vues(session)
    await update.message.reply_text(await asyncio.to_thread(calcul))


def installer(app) -> None:
    for nom, fonction in (("stats", commande_stats), ("chrono", commande_chrono), ("fils", commande_fils), ("afaire", commande_afaire),
                          ("gopro", commande_gopro), ("kit", commande_kit), ("episode", commande_episode), ("vues", commande_vues)):
        app.add_handler(CommandHandler(nom, fonction))
