"""Couche Telegram des surprises : /easter, /chambre, commandes cachées, événements du moment, gestion d'erreur."""
import asyncio
import logging
import random

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, amis, config, ia, surprises
from .db import SessionLocal
from .models import Personne

log = logging.getLogger("agentski")
ENVOI_MEDIA = {"photo": "send_photo", "video": "send_video", "animation": "send_animation", "document": "send_document"}


def _personne(session, update: Update):
    return acces.personne_connue(session, update.effective_user.id)


async def envoyer_media(bot, chat_id: int, type_: str, file_id: str, legende: str | None = None) -> None:
    await getattr(bot, ENVOI_MEDIA.get(type_, "send_document"))(chat_id, file_id, caption=legende or None)


async def commande_easter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.message.reply_text("Fais-le en privé avec moi, pour ne pas gâcher la surprise : écris-moi /easter.")
        return
    with SessionLocal() as session:
        surprises.commencer_archive(session, _personne(session, update))
    await update.message.reply_text("🥚 Envoie-moi la photo, la vidéo ou le GIF (moins de 20 Mo). Il ressortira au hasard, "
                                    "avec une ligne improvisée.")


async def media_pour_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True si ce média était attendu pour une archive d'easter egg (et a donc été pris en charge)."""
    m = update.message
    with SessionLocal() as session:
        personne = _personne(session, update)
        archive = surprises.archive_en_attente(session, personne, "media")
        if archive is None:
            return False
        if m.photo:
            type_, fichier = "photo", m.photo[-1]
        elif m.video:
            type_, fichier = "video", m.video
        elif m.animation:
            type_, fichier = "animation", m.animation
        else:
            type_, fichier = "document", m.document
        suite = surprises.recevoir_media(session, archive, type_, fichier.file_id, fichier.file_size)
    if isinstance(suite, str):
        await m.reply_text(suite)
    else:
        await m.reply_text(suite.texte, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in suite.boutons]))
    return True


async def bouton_cible(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, archive_id, personne_id = query.data.split("|")
    with SessionLocal() as session:
        question = surprises.choisir_cible(session, int(archive_id), int(personne_id), _personne(session, update))
    await query.answer()
    if question is None:
        await query.edit_message_text("Cette archive n'est plus en cours d'ajout.")
        return
    await query.edit_message_text("Cible choisie.")
    envoye = await context.bot.send_message(query.message.chat_id, question, reply_markup=ForceReply(
        selective=True, input_field_placeholder="Ce qu'on voit…"))
    with SessionLocal() as session:
        surprises.enregistrer_message_archive(session, int(archive_id), envoye.message_id)


async def description_archive(update: Update, context: ContextTypes.DEFAULT_TYPE, reponse_a: int) -> bool:
    with SessionLocal() as session:
        reponse = surprises.decrire(session, _personne(session, update), reponse_a, update.message.text)
    if reponse is None:
        return False
    await update.message.reply_text(reponse)
    return True


def _chambrer(cible: str | None):
    with SessionLocal() as session:
        return surprises.chambrer(session, cible, ia.creatif)


def _compter_chambre(telegram_id: int) -> None:
    """Compteur pour le trophée Chambreur."""
    with SessionLocal() as session:
        personne = acces.personne_connue(session, telegram_id)
        cle = f"chambre_count:{personne.id}"
        amis.definir_reglage(session, cle, str(int(amis.reglage(session, cle) or 0) + 1))


async def commande_chambre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _compter_chambre(update.effective_user.id)
    resultat = await asyncio.to_thread(_chambrer, " ".join(context.args) or None)
    if isinstance(resultat, str):
        await update.message.reply_text(resultat)
        return
    await envoyer_media(context.bot, update.effective_chat.id, *resultat.media, legende=resultat.texte)


async def commande_verte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(surprises.podium_verte(session))


async def commande_raclette(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(await asyncio.to_thread(surprises.fait_fromage, ia.creatif))


async def commande_aide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .commandes import AIDE
    if random.random() < 1 / config.AIDE_NON_UNE_FOIS_SUR:
        await update.message.reply_text("Non.")
        context.job_queue.run_once(lambda ctx: ctx.bot.send_message(update.effective_chat.id, AIDE), 10)
        return
    await update.message.reply_text(AIDE)


async def commande_anniversaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(surprises.regler_anniversaire(session, _personne(session, update), " ".join(context.args)))


def _evenements() -> list[surprises.Evenement]:
    from . import agenda, bonnenuit, briefing, positions, trophees
    with SessionLocal() as session:
        positions.maintenance(session)  # déduit les pistes du soir et efface les positions du week-end terminé
        return (surprises.evenements(session, ia.creatif) + briefing.evenements_meteo(session, ia.creatif)
                + agenda.question_alix(session) + trophees.evenements(session) + bonnenuit.evenements(session, ia.creatif))


async def envoyer_evenements(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Appelé à chaque minute : envoie les surprises du moment, une erreur n'arrête jamais la boucle."""
    try:
        evenements = await asyncio.to_thread(_evenements)
        with SessionLocal() as session:
            groupe = amis.groupe_id(session)
        for e in evenements:
            chat = groupe if e.destinataire == "groupe" else e.destinataire
            clavier = InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in e.boutons]) if e.boutons else None
            if e.media:
                await envoyer_media(context.bot, chat, *e.media, legende=e.texte)
            else:
                await context.bot.send_message(chat, e.texte, reply_markup=clavier)
    except Exception:
        log.exception("Échec des surprises du moment")


async def erreur(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """En cas d'erreur technique, le bot se chambre lui-même."""
    log.error("Erreur non gérée", exc_info=context.error)
    chat = getattr(update, "effective_chat", None)
    if chat is not None:
        try:
            await context.bot.send_message(chat.id, "Désolé, je suis tombé. Sur une verte. 🙈")
        except Exception:
            pass


def installer(app) -> None:
    app.add_handler(CommandHandler("easter", commande_easter))
    app.add_handler(CommandHandler("chambre", commande_chambre))
    app.add_handler(CommandHandler("verte", commande_verte))
    app.add_handler(CommandHandler("raclette", commande_raclette))
    app.add_handler(MessageHandler(filters.Regex("^🧀$"), commande_raclette))
    app.add_handler(CommandHandler("aide", commande_aide))
    app.add_handler(CommandHandler("anniversaire", commande_anniversaire))
    app.add_handler(CallbackQueryHandler(bouton_cible, pattern=r"^x\|"))
    app.add_error_handler(erreur)
