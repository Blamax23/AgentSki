import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, TypeHandler, filters)

from sqlalchemy import select

from . import acces, config, rappels, simulation
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
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, echo))
    app.job_queue.run_repeating(tic, interval=60, first=5)
    app.run_polling()
