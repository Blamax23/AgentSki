"""Couche Telegram des connexions : /son et liens Spotify, /ics, réponse d'Alix."""
import asyncio
import io

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, agenda, config, spotify
from .amis import definir_reglage
from .db import SessionLocal
from .models import Personne
from sqlalchemy import select


def _ajouter(texte: str, telegram_id: int) -> str:
    with SessionLocal() as session:
        return spotify.ajouter_morceau(session, acces.personne_connue(session, telegram_id), texte)


def _clavier(boutons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in boutons])


async def _chercher_ou_agir(update: Update, texte: str, action: str, direct) -> None:
    """Un lien Spotify est exact : on agit tout de suite. Un titre écrit donne trois propositions à choisir."""
    if not texte.strip() or spotify.extraire_id(texte):
        await update.message.reply_text(await asyncio.to_thread(direct, texte, update.effective_user.id))
        return
    message, boutons = await asyncio.to_thread(spotify.proposer, texte, action, update.effective_user.id)
    await update.message.reply_text(message, reply_markup=_clavier(boutons) if boutons else None)


async def commande_son(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _chercher_ou_agir(update, " ".join(context.args), "son", _ajouter)


async def bouton_morceau(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Choix parmi les propositions : seule la personne qui a lancé la recherche peut trancher."""
    query = update.callback_query
    _, action, track_id, demandeur = query.data.split("|")
    if update.effective_user.id != int(demandeur):
        await query.answer("C'est le choix de la personne qui a lancé la recherche.", show_alert=True)
        return
    await query.answer()
    if action == "x":
        await query.edit_message_text("Recherche annulée. Essaie avec plus de précisions, ou colle un lien Spotify.")
        return
    lien = f"spotify:track:{track_id}"
    await query.edit_message_text(f"{query.message.text.splitlines()[0]}\n✅ Choix enregistré.")
    reponse = await asyncio.to_thread({"son": _ajouter, "play": _lancer, "file": _file}[action], lien, update.effective_user.id)
    await context.bot.send_message(query.message.chat_id, reponse)


def _lancer(texte: str, telegram_id: int) -> str:
    return spotify.lancer_musique(texte)


def _file(texte: str, telegram_id: int) -> str:
    return spotify.mettre_en_file(texte)


async def commande_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _chercher_ou_agir(update, " ".join(context.args), "file", _file)


async def commande_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _chercher_ou_agir(update, " ".join(context.args), "play", _lancer)


async def commande_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(await asyncio.to_thread(spotify.mettre_en_pause))


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
    app.add_handler(CallbackQueryHandler(bouton_morceau, pattern=r"^s\|"))
    app.add_handler(CommandHandler("play", commande_play))
    app.add_handler(CommandHandler("file", commande_file))
    app.add_handler(CommandHandler("pause", commande_pause))
    app.add_handler(CommandHandler("ics", commande_ics))
    app.add_handler(CallbackQueryHandler(bouton_alix, pattern=r"^al\|"))
    app.add_handler(MessageHandler(filters.Regex(spotify.LIEN) & ~filters.COMMAND, lien_spotify))
