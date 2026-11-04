import asyncio
import logging
import re
import tempfile
from datetime import datetime, timezone

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, TypeHandler, filters)

from sqlalchemy import select

from . import acces, audio, config, entretien, ia, journal, rappels, simulation
from .db import SessionLocal, seed_personnes, sync_allowed_users
from .models import Personne

log = logging.getLogger("agentski")


def clavier(message: rappels.Message) -> InlineKeyboardMarkup | None:
    if not message.boutons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne]
                                 for ligne in message.boutons])


tentatives = acces.Tentatives()


async def liste_blanche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un compte connu passe. Un inconnu reçoit la question d'accès : bonne réponse, puis il choisit qui il est."""
    user = update.effective_user
    if user is None:
        raise ApplicationHandlerStop
    with SessionLocal() as session:
        if acces.personne_connue(session, user.id) is not None:
            return
    await accueillir_inconnu(update, context)
    raise ApplicationHandlerStop


async def accueillir_inconnu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not acces.acces_actif() or (chat is not None and chat.type != "private"):
        # Pas de question dans un groupe : elle serait posée devant tout le monde
        log.warning("Inconnu ignoré : id=%s prénom=%s", user.id, user.first_name)
        return
    maintenant = datetime.now(timezone.utc).replace(tzinfo=None)  # heure réelle, même si l'horloge de test est figée
    if tentatives.bloque(user.id, maintenant):
        return

    query = update.callback_query
    if query is not None:
        # Choix du prénom, seulement après une bonne réponse
        if query.data.startswith("acces|") and context.user_data.get("verifie"):
            prenom = query.data.split("|", 1)[1]
            with SessionLocal() as session:
                personne = acces.rattacher(session, user.id, prenom)
            await query.answer()
            if personne is None:
                await query.edit_message_text("Ce prénom est déjà pris. Envoie-moi un message pour recommencer.")
                context.user_data.clear()
                return
            await query.edit_message_text(f"Bienvenue {personne.prenom} ! Tu peux me parler.")
            log.warning("Accès accordé : id=%s (%s) rattaché à %s", user.id, user.first_name, personne.prenom)
            await prevenir_organisateur(context, f"{user.first_name} (Telegram {user.id}) a répondu juste et "
                                                 f"s'est déclaré·e {personne.prenom}.")
        return

    if update.message is None or not update.message.text:
        return
    if not context.user_data.get("question_posee"):
        context.user_data["question_posee"] = True
        log.warning("Inconnu : id=%s prénom=%s, question posée", user.id, user.first_name)
        await update.message.reply_text(f"Salut ! Pour entrer, une question :\n{config.QUESTION_ACCES}")
        return
    if acces.reponse_correcte(update.message.text):
        tentatives.reussir(user.id)
        context.user_data["verifie"] = True
        with SessionLocal() as session:
            libres = acces.personnes_libres(session)
        if not libres:
            await update.message.reply_text("Bonne réponse, mais toutes les places sont prises. Demande à Maxime.")
            return
        await update.message.reply_text("Bonne réponse ! Qui es-tu ?", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(p.prenom, callback_data=f"acces|{p.prenom}")] for p in libres]))
        return
    restant = tentatives.rater(user.id, maintenant)
    log.warning("Mauvaise réponse d'accès : id=%s prénom=%s, essais restants %s", user.id, user.first_name, restant)
    if restant:
        await update.message.reply_text(f"Raté. Il te reste {restant} essai{'s' if restant > 1 else ''}.")
    else:
        await update.message.reply_text(f"Trop d'essais ratés. Réessaie dans {config.BLOCAGE_MIN} minutes.")


async def prevenir_organisateur(context: ContextTypes.DEFAULT_TYPE, texte: str) -> None:
    """Informe Maxime d'une nouvelle arrivée : n'importe qui connaissant la réponse peut se dire n'importe qui."""
    with SessionLocal() as session:
        orga = session.scalar(select(Personne).where(Personne.prenom == config.ORGANISATEUR))
        destinataire = orga.telegram_id if orga else None
    if destinataire:
        await context.bot.send_message(destinataire, "🔔 " + texte)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        prenom = acces.personne_connue(session, update.effective_user.id).prenom
    await update.message.reply_text(f"Salut {prenom}, je suis là.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Reçu. (Le socle répond, le reste arrive aux étapes suivantes.)")


async def bouton_jeudi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    with SessionLocal() as session:
        message = rappels.repondre(session, query.data)
    await query.answer()
    await query.edit_message_text(message.texte, reply_markup=clavier(message))


async def envoyer(chat_id: int, context: ContextTypes.DEFAULT_TYPE, messages: list[rappels.Message]) -> None:
    for m in messages:
        await context.bot.send_message(chat_id, m.texte, reply_markup=clavier(m))


async def bouton_entretien(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        reaction = entretien.bouton(session, query.data, personne.id)
    await query.answer()
    if reaction.edition is not None:
        await query.edit_message_text(reaction.edition.texte, reply_markup=clavier(reaction.edition))
    await envoyer(query.message.chat_id, context, reaction.suite)


def _traiter(entretien_id: int, texte: str, duree: float | None, cout: float | None) -> list[rappels.Message]:
    """Extraction et suite de l'entretien (appels réseau bloquants : à lancer dans un fil)."""
    with SessionLocal() as session:
        e = session.get(entretien.Entretien, entretien_id)
        return entretien.recevoir_transcription(session, e, texte, duree, cout, ia.extraire)


async def traiter_transcription(chat_id: int, context: ContextTypes.DEFAULT_TYPE, entretien_id: int, texte: str,
                                duree: float | None = None, cout: float | None = None) -> None:
    extrait = texte if len(texte) <= 600 else texte[:600] + "…"
    await context.bot.send_message(chat_id, f"📝 J'ai compris : « {extrait} »")
    messages = await asyncio.to_thread(_traiter, entretien_id, texte, duree, cout)
    await envoyer(chat_id, context, messages)


async def vocal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media = update.message.voice or update.message.audio
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        e = entretien.entretien_ouvert(session, personne.id)
        entretien_id = e.id if e else None
    if entretien_id is None:
        await update.message.reply_text("Je n'ai pas d'entretien en cours avec toi. "
                                        "Le bouton « Commencer l'entretien » arrive avec le rappel du soir.")
        return
    if media.file_size and media.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("Trop lourd, envoie une version plus courte.")
        return
    await update.message.reply_text("🎧 Je t'écoute, ça prend quelques secondes…")
    try:
        with tempfile.TemporaryDirectory() as dossier:  # l'audio est supprimé dès la fin du bloc
            source = Path(dossier) / "vocal.ogg"
            await (await media.get_file()).download_to_drive(source)
            morceaux = await asyncio.to_thread(audio.en_morceaux_mp3, source, Path(dossier))
            texte, cout = await asyncio.to_thread(ia.transcrire, morceaux)
    except Exception:
        log.exception("Transcription échouée")
        await update.message.reply_text("Je n'ai pas réussi à transcrire ce vocal. Renvoie-le, ou réessaie dans un instant.")
        return
    if not texte:
        await update.message.reply_text("Je n'ai rien entendu dans ce vocal. Tu peux le refaire ?")
        return
    await traiter_transcription(update.effective_chat.id, context, entretien_id, texte, media.duration, cout)


async def texte_entretien(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un texte pendant un entretien : le nom d'une piste à confirmer. Sinon, réponse par défaut."""
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        e = entretien.entretien_ouvert(session, personne.id)
        messages = entretien.saisir_piste(session, e, update.message.text) if e else []
    if not messages:
        await echo(update, context)
        return
    await envoyer(update.effective_chat.id, context, messages)


async def commande_journal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        cible = datetime.strptime(context.args[0], "%Y-%m-%d").date() if context.args else None
    except ValueError:
        await update.message.reply_text("Format : /journal, ou /journal 2026-12-12")
        return
    with SessionLocal() as session:
        texte = journal.journal_md(session, cible)
    for i in range(0, len(texte), 4000):
        await update.message.reply_text(texte[i:i + 4000])


async def supprime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """« /supprime 12 », « supprime #12 », ou « supprime ça » en réponse à un message qui contient un seul #numéro."""
    texte = update.message.text
    numero = journal.numero_cite(re.sub(r"^/?supprime\S*", "", texte, flags=re.I))
    if numero is None and update.message.reply_to_message and update.message.reply_to_message.text:
        cites = re.findall(r"#(\d+)", update.message.reply_to_message.text)
        numero = int(cites[0]) if len(cites) == 1 else None
    if numero is None:
        await update.message.reply_text("Quel élément ? Dis « supprime 12 » avec le numéro du /journal, "
                                        "ou réponds « supprime ça » à un message qui n'en contient qu'un.")
        return
    with SessionLocal() as session:
        await update.message.reply_text(journal.supprimer(session, numero))


async def tic(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chaque minute : prévoit le prochain jeudi, puis envoie les rappels dont l'heure est passée."""
    with SessionLocal() as session:
        rappels.planifier_jeudi(session)
        for envoi in rappels.a_envoyer(session):
            if envoi.telegram_id is None:
                continue  # destinataire sans /start : réessayé jusqu'à l'abandon
            try:
                await context.bot.send_message(envoi.telegram_id, envoi.message.texte,
                                               reply_markup=clavier(envoi.message))
            except Exception:
                log.exception("Échec d'envoi du rappel %s, nouvel essai à la prochaine minute", envoi.rappel_id)
                continue
            rappels.marquer_envoye(session, envoi.rappel_id)


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # ses logs contiennent le token dans l'URL
    with SessionLocal() as session:
        seed_personnes(session)
        sync_allowed_users(session)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(TypeHandler(Update, liste_blanche), group=-1)
    app.add_handler(CommandHandler("start", start))
    simulation.installer(app)  # fausses commandes, à retirer au gel de l'étape 4
    app.add_handler(CallbackQueryHandler(bouton_jeudi, pattern=r"^j\|"))
    app.add_handler(CallbackQueryHandler(bouton_entretien, pattern=r"^e\|"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, vocal))
    app.add_handler(CommandHandler("journal", commande_journal))
    app.add_handler(CommandHandler("supprime", supprime))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^\s*supprime"), supprime))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texte_entretien))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, echo))
    app.job_queue.run_repeating(tic, interval=60, first=5)
    app.run_polling()
