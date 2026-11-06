"""Couche Telegram des connexions : /son et liens Spotify, /ics, réponse d'Alix."""
import asyncio
import io

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, agenda, amis, config, jam, spotify
from .amis import definir_reglage
from .db import SessionLocal
from .models import Morceau, Personne
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


async def commande_jam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/jam : le Jam en cours ; /jam lien : l'hôte le partage à tout le monde ; /jam fin : le Jam est terminé."""
    texte_args = " ".join(context.args).strip()
    envois: list[tuple[int, str]] = []  # (conversation, message) à envoyer aux autres une fois le Jam enregistré
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        if not texte_args:
            reponse = jam.texte_jam(session)
        elif texte_args.lower() in ("fin", "stop", "termine", "terminé"):
            if jam.hote_id(session) not in (None, personne.id) and personne.prenom != config.ORGANISATEUR:
                reponse = "Seul l'hôte du Jam (ou l'organisateur) peut le déclarer terminé."
            else:
                reponse = "🏁 Jam terminé, merci à tous." if jam.terminer(session) else "Aucun Jam en cours."
        else:
            lien = jam.extraire_lien(texte_args)
            if lien is None:
                reponse = "Colle le lien d'invitation du Jam (open.spotify.com/… ou spotify.link/…) : /jam lien"
            else:
                jam.definir(session, personne, lien)
                annonce = jam.texte_jam(session, annonce=True)
                groupe = amis.groupe_id(session)
                cibles = {groupe} if groupe else set()
                cibles |= {p.telegram_id for p in session.scalars(select(Personne).where(Personne.telegram_id.is_not(None))) if p.id != personne.id}
                cibles.discard(update.effective_chat.id)  # la personne qui tape la commande reçoit déjà la réponse ici
                envois = [(c, annonce) for c in cibles]
                reponse = "✅ Jam partagé avec tout le monde."
    for conversation, message in envois:
        try:
            await context.bot.send_message(conversation, message)
        except Exception:
            pass  # quelqu'un qui n'a jamais écrit au bot ne peut pas recevoir de message privé
    await update.message.reply_text(reponse)


async def commande_reset_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        nb = len(session.scalars(select(Morceau.id)).all())
    await update.message.reply_text(
        f"Playlist « {config.PLAYLIST_NOM} » : {nb} morceau{'x' if nb > 1 else ''} connu{'s' if nb > 1 else ''} du bot. Réinitialiser lui fait tout oublier "
        "(morceaux, blind test, doublons) et le prochain /son créera une nouvelle playlist. Rien n'est supprimé sur Spotify.",
        reply_markup=_clavier([[("🧹 Réinitialiser", "pl|oui"), ("Annuler", "pl|non")]]))


async def bouton_reset_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        if personne is None or personne.prenom != config.ORGANISATEUR:
            await query.answer("Réservé à l'organisateur.", show_alert=True)
            return
        texte = spotify.reinitialiser(session) if query.data == "pl|oui" else "Annulé, rien n'a changé."
    await query.answer()
    await query.edit_message_text(texte)


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
    app.add_handler(CommandHandler("jam", commande_jam))
    app.add_handler(CommandHandler("reset_playlist", commande_reset_playlist))
    app.add_handler(CallbackQueryHandler(bouton_reset_playlist, pattern=r"^pl\|"))
    app.add_handler(CallbackQueryHandler(bouton_alix, pattern=r"^al\|"))
    app.add_handler(MessageHandler(filters.Regex(spotify.LIEN) & ~filters.COMMAND, lien_spotify))
