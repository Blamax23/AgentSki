"""Couche Telegram du blind test : /blindtest [manches], /blindtest stop, bouton « Révéler », réponses écrites dans le groupe."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import acces, blindtest, spotify
from .db import SessionLocal

DELAI_MANCHE_SUIVANTE = 8  # secondes entre une révélation et la manche suivante


def _clavier() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Révéler", callback_data="bt|rev")]])


def _supprimer_jobs(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    for nom in (f"bt_pause:{chat_id}", f"bt_rev:{chat_id}", f"bt_suite:{chat_id}"):
        for job in context.job_queue.get_jobs_by_name(nom):
            job.schedule_removal()


async def lancer_manche(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    def calcul():
        with SessionLocal() as session:
            return blindtest.lancer_manche(session, chat_id)
    manche = await asyncio.to_thread(calcul)
    if isinstance(manche, str):
        await context.bot.send_message(chat_id, manche)
        return
    await context.bot.send_message(chat_id, f"🎧 Manche {manche.numero}/{manche.total} : écoutez ! Écrivez le titre ({blindtest.POINTS_TITRE} pts) "
                                            f"ou l'artiste ({blindtest.POINTS_ARTISTE} pt) dans le groupe.", reply_markup=_clavier())
    context.job_queue.run_once(_pause, blindtest.SECONDES_ECOUTE, data=chat_id, name=f"bt_pause:{chat_id}")
    context.job_queue.run_once(_revelation_auto, blindtest.SECONDES_MANCHE, data=(chat_id, manche.jeton), name=f"bt_rev:{chat_id}")


async def _pause(context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(spotify.mettre_en_pause)


async def _revelation_auto(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, jeton = context.job.data
    with SessionLocal() as session:
        if blindtest.manche_ouverte(session, chat_id) != jeton:
            return  # déjà révélée (bouton ou tout trouvé)
    await reveler(context, chat_id)


async def reveler(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    _supprimer_jobs(context, chat_id)
    with SessionLocal() as session:
        resultat = blindtest.reveler(session, chat_id)
    if resultat is None:
        return
    texte, fini = resultat
    await asyncio.to_thread(spotify.mettre_en_pause)
    await context.bot.send_message(chat_id, texte)
    if not fini:
        context.job_queue.run_once(_suite, DELAI_MANCHE_SUIVANTE, data=chat_id, name=f"bt_suite:{chat_id}")


async def _suite(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    with SessionLocal() as session:
        if not blindtest.en_cours(session, chat_id):
            return  # arrêté entre-temps
    await lancer_manche(context, chat_id)


async def commande_blindtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if context.args and context.args[0].lower() in ("stop", "arret", "arrêt"):
        _supprimer_jobs(context, chat_id)
        with SessionLocal() as session:
            reponse = blindtest.arreter(session, chat_id)
        await asyncio.to_thread(spotify.mettre_en_pause)
        await update.message.reply_text(reponse)
        return
    try:
        manches = int(context.args[0]) if context.args else None
    except ValueError:
        await update.message.reply_text("Format : /blindtest, /blindtest 8 (nombre de manches), ou /blindtest stop.")
        return
    with SessionLocal() as session:
        erreur = blindtest.demarrer(session, chat_id, manches)
        total = None if erreur else blindtest.total_manches(session, chat_id)
    if erreur:
        await update.message.reply_text(erreur)
        return
    await update.message.reply_text(f"🎶 Blind test sur la playlist du week-end annuel : {total} manches. Spotify doit jouer sur un appareil allumé "
                                    "(téléphone, enceinte). Je lance la première manche…")
    await lancer_manche(context, chat_id)


async def bouton_reveler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await reveler(context, update.effective_chat.id)


async def deviner(update: Update, context: ContextTypes.DEFAULT_TYPE, texte: str) -> None:
    """Un message du groupe pendant une manche : s'il trouve le titre ou l'artiste, Krok l'annonce. Sinon, silence."""
    chat_id = update.effective_chat.id
    with SessionLocal() as session:
        if blindtest.manche_ouverte(session, chat_id) is None:
            return
        joueur = acces.personne_connue(session, update.effective_user.id)
        resultat = blindtest.proposer(session, chat_id, joueur, texte) if joueur else None
    if resultat is None:
        return
    await update.message.reply_text(resultat.texte)
    if resultat.termine:
        await reveler(context, chat_id)


def installer(app) -> None:
    app.add_handler(CommandHandler("blindtest", commande_blindtest))
    app.add_handler(CallbackQueryHandler(bouton_reveler, pattern=r"^bt\|"))
