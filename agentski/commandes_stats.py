"""Couche Telegram des statistiques : /stats, /chrono, /fils, /afaire, /gopro, /kit, /episode, /vues."""
import asyncio
import io
from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, activites, amis, stats, youtube
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


def _analyser_fichier(telegram_id: int, nom: str, octets: bytes) -> str:
    with SessionLocal() as session:
        return activites.enregistrer(session, acces.personne_connue(session, telegram_id), nom, octets)


async def fichier_activite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un fichier .fit ou .gpx envoyé par n'importe quelle personne autorisée : descentes, dénivelé, vitesse, rangés dans sa journée."""
    document = update.message.document
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("Trop lourd (plus de 20 Mo) : je ne peux pas le télécharger.")
        return
    octets = bytes(await (await document.get_file()).download_as_bytearray())
    await update.message.reply_text(await asyncio.to_thread(_analyser_fichier, update.effective_user.id, document.file_name or "activite.fit", octets))


def installer(app) -> None:
    app.add_handler(MessageHandler(filters.Document.FileExtension("fit") | filters.Document.FileExtension("gpx"), fichier_activite))
    for nom, fonction in (("stats", commande_stats), ("chrono", commande_chrono), ("fils", commande_fils), ("afaire", commande_afaire),
                          ("gopro", commande_gopro), ("kit", commande_kit), ("episode", commande_episode), ("vues", commande_vues)):
        app.add_handler(CommandHandler(nom, fonction))
