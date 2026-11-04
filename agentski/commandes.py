"""Commandes Telegram de l'accueil des amis : /start, /groupe, /moment, /chute, /citation, médias, note du soir, capsule."""
from telegram import ForceReply, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, amis, config
from .db import SessionLocal

AIDE = ("Voilà ce que je sais faire :\n"
        "• /moment [texte] : poser un marqueur dans la journée\n"
        "• /chute [prénom] [verte] : noter une chute (la tienne par défaut)\n"
        "• /easter : ajouter une archive à la réserve de surprises (de préférence en privé)\n"
        "• /chambre [prénom] : une archive tout de suite\n"
        "• /citation [prénom] \"phrase\" : garder une phrase culte\n"
        "• Envoie-moi tes photos et vidéos de moins de 20 Mo, je les range par journée.\n"
        "• Le soir, je te demande ta note sur 10 et une phrase.")


def _personne(session, update: Update):
    return acces.personne_connue(session, update.effective_user.id)


def _est_organisateur(personne) -> bool:
    return personne is not None and personne.prenom == config.ORGANISATEUR


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        personne = _personne(session, update)
        question = amis.demarrer_capsule(session, personne)
    await update.message.reply_text(f"Salut {personne.prenom}, je suis là.\n\n{AIDE}")
    if question:
        await poser_capsule(update.effective_chat.id, context, personne.id, question)


async def poser_capsule(chat_id: int, context: ContextTypes.DEFAULT_TYPE, personne_id: int, question: str) -> None:
    """Pose une question de capsule en réponse forcée : la réponse est reconnue, même au milieu d'autres messages."""
    envoye = await context.bot.send_message(chat_id, question, reply_markup=ForceReply(
        selective=True, input_field_placeholder="Ta réponse…"))
    from .models import Personne
    with SessionLocal() as session:
        amis.enregistrer_message_capsule(session, session.get(Personne, personne_id), envoye.message_id)


async def commande_capsule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.message.reply_text("La capsule se remplit en privé : écris-moi directement.")
        return
    with SessionLocal() as session:
        personne = _personne(session, update)
        question = amis.demarrer_capsule(session, personne)
    if question is None:
        await update.message.reply_text("🔒 Ta capsule est déjà scellée.")
        return
    await poser_capsule(update.effective_chat.id, context, personne.id, question)


async def commande_revelation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Réservée à l'organisateur : révèle les capsules une par une dans le groupe (ou ici si aucun groupe)."""
    with SessionLocal() as session:
        if not _est_organisateur(_personne(session, update)):
            return
        textes = amis.reveler_capsules(session)
        cible = amis.groupe_id(session) or update.effective_chat.id
    if not textes:
        await update.message.reply_text("Aucune capsule complète à révéler.")
        return
    for texte in textes:
        await context.bot.send_message(cible, texte)


async def commande_groupe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        if not _est_organisateur(_personne(session, update)):
            return
        if update.effective_chat.type == "private":
            await update.message.reply_text("Envoie /groupe dans le groupe des participants, après y avoir ajouté le bot.")
            return
        amis.definir_reglage(session, "groupe_id", str(update.effective_chat.id))
    await update.message.reply_text("✅ Ce groupe est enregistré : j'y publierai les annonces.")


async def commande_moment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        reponse = amis.ajouter_moment(session, _personne(session, update), " ".join(context.args))
    await update.message.reply_text(reponse)


async def commande_chute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mots = [m for m in context.args if m.lower() != "verte"]
    verte = len(mots) != len(context.args)
    with SessionLocal() as session:
        reponse = amis.ajouter_chute(session, _personne(session, update), " ".join(mots) or None, verte)
    await update.message.reply_text(reponse)


async def commande_citation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        reponse = amis.ajouter_citation(session, _personne(session, update), context.args)
    await update.message.reply_text(reponse)


async def media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.message
    from . import fun
    if await fun.media_pour_archive(update, context):  # un média attendu pour l'ajout d'une archive d'easter egg
        return
    if m.photo:
        type_, fichier = "photo", m.photo[-1]
    elif m.video:
        type_, fichier = "video", m.video
    elif m.animation:
        type_, fichier = "animation", m.animation
    else:
        type_, fichier = "document", m.document
    with SessionLocal() as session:
        reponse = amis.ranger_media(session, _personne(session, update), type_, fichier.file_id, fichier.file_size, m.caption)
    await m.reply_text(reponse)


async def bouton_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, journee_id, valeur = query.data.split("|")
    with SessionLocal() as session:
        note = amis.enregistrer_note(session, _personne(session, update), int(journee_id), int(valeur))
        note_id = note.id
    await query.answer()
    await query.edit_message_text(f"⭐ Note : {valeur}/10")
    envoye = await context.bot.send_message(query.message.chat_id, "Et une phrase sur ta journée ? (Réponds à ce message)",
                                            reply_markup=ForceReply(selective=True, input_field_placeholder="Ta phrase…"))
    from .models import Note
    with SessionLocal() as session:
        session.get(Note, note_id).message_id = envoye.message_id
        session.commit()


async def texte_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un texte : réponse à la phrase du soir ou à la capsule si c'est une réponse à ces questions, sinon l'entretien."""
    reponse_a = update.message.reply_to_message.message_id if update.message.reply_to_message else None
    if reponse_a is not None:
        from . import fun
        if await fun.description_archive(update, context, reponse_a):
            return
        with SessionLocal() as session:
            personne = _personne(session, update)
            phrase = amis.enregistrer_phrase(session, personne, reponse_a, update.message.text)
            capsule = None if phrase else amis.repondre_capsule(session, personne, reponse_a, update.message.text)
        if phrase:
            await update.message.reply_text(phrase)
            return
        if capsule:
            confirmation, suite = capsule
            await update.message.reply_text(confirmation)
            if suite:
                await poser_capsule(update.effective_chat.id, context, personne.id, suite)
            return
    from .bot import texte_entretien
    await texte_entretien(update, context)


def installer(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("groupe", commande_groupe))
    app.add_handler(CommandHandler("moment", commande_moment))
    app.add_handler(CommandHandler("chute", commande_chute))
    app.add_handler(CommandHandler("citation", commande_citation))
    app.add_handler(CommandHandler("capsule", commande_capsule))
    app.add_handler(CommandHandler("revelation", commande_revelation))
    app.add_handler(CallbackQueryHandler(bouton_note, pattern=r"^n\|"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.IMAGE
                                   | filters.Document.VIDEO, media))
