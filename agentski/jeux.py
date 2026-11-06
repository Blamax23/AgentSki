"""Couche Telegram des idées, paris, courses, suggestions et défis."""
import asyncio

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import acces, amis, defis, ia, plans
from .db import SessionLocal


def _clavier(boutons) -> InlineKeyboardMarkup | None:
    if not boutons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in boutons])


def _avec_session(fonction, *args):
    """Exécute `fonction(session, *args)` dans un fil : les appels au modèle sont bloquants."""
    with SessionLocal() as session:
        return fonction(session, *args)


def _moi(update: Update):
    with SessionLocal() as session:
        return acces.personne_connue(session, update.effective_user.id)


async def commande_idee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_avec_session(plans.ajouter_idee, _moi(update), " ".join(context.args)))


async def commande_suggestion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔎 Je cherche…")
    texte = await asyncio.to_thread(_avec_session, plans.suggestion, " ".join(context.args), ia.recherche_web)
    await update.message.reply_text(texte, disable_web_page_preview=True)


async def commande_pari(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_avec_session(plans.ajouter_pari, _moi(update), " ".join(context.args)))


async def commande_paris(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_avec_session(plans.paris_ouverts))


async def commande_resolu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        numero, gagnant = int(context.args[0].lstrip("#")), " ".join(context.args[1:])
    except (IndexError, ValueError):
        await update.message.reply_text("Format : /resolu 3 Adrien")
        return
    await update.message.reply_text(_avec_session(plans.resoudre_pari, numero, gagnant))


async def commande_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(await asyncio.to_thread(_avec_session, plans.liste_courses, ia.economique))
    elif context.args[0].lower() == "vider":
        await update.message.reply_text(_avec_session(plans.vider_courses))
    else:
        await update.message.reply_text(await asyncio.to_thread(_avec_session, plans.course, _moi(update), " ".join(context.args), ia.economique))


async def commande_defi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    boutons = [[(nom + ("" if dispo else " (bientôt)"), f"df|{cle}")] for cle, (nom, _, _, dispo) in defis.TYPES.items()]
    await update.message.reply_text("Quel défi ? Il ne contraint jamais le ski : télésiège, pause, chalet ou soirée.",
                                    reply_markup=_clavier(boutons))


async def bouton_lancer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    type_ = query.data.split("|")[1]
    resultat = await asyncio.to_thread(_avec_session, defis.lancer, type_, ia.creatif)
    await query.answer()
    if isinstance(resultat, str):
        await query.edit_message_text(resultat)
        return
    await query.edit_message_text(f"{defis.TYPES[type_][0]} lancé.")
    with SessionLocal() as session:
        groupe = amis.groupe_id(session)
    chat = groupe or query.message.chat_id
    if resultat.media:
        from .fun import envoyer_media
        await envoyer_media(context.bot, chat, *resultat.media)
    await context.bot.send_message(chat, resultat.texte, reply_markup=_clavier(resultat.boutons))
    for telegram_id, message in resultat.prives:
        try:
            await context.bot.send_message(telegram_id, message.texte, reply_markup=_clavier(message.boutons))
        except Exception:
            pass  # quelqu'un qui n'a jamais écrit au bot ne peut pas recevoir de message privé


async def bouton_vote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, defi_id, valeur = query.data.split("|")
    with SessionLocal() as session:
        votant = acces.personne_connue(session, update.effective_user.id)
        message = defis.voter(session, int(defi_id), votant, valeur)
        raison = defis.raison_du_refus(session, int(defi_id), votant) if message is None else ""
    if message is None:
        await query.answer(raison, show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(message.texte, reply_markup=_clavier(message.boutons))


async def commande_ajout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.message.reply_text("Ajoute tes éléments en privé avec moi : ton nom reste caché, et la surprise aussi.")
        return
    boutons = [[(nom, f"a|{cle}")] for cle, nom in defis.types_personnalisables()]
    await update.message.reply_text("Dans quelle réserve veux-tu ajouter un élément ?", reply_markup=_clavier(boutons))


async def bouton_ajout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    type_ = query.data.split("|")[1]
    await query.answer()
    consignes = {"phrase": "la phrase absurde à placer face caméra", "chef": "le plat", "quiz": "la question, puis ||| et la réponse",
                 "gage": "le gage (jamais pendant une descente, ni gênant pour la vue ou l'équipement)",
                 "debat": "la question absurde", "pronostic": "la question à réponse oui ou non sur la journée"}
    await query.edit_message_text(f"Réserve « {defis.TYPES[type_][0]} ».")
    envoye = await context.bot.send_message(query.message.chat_id, f"Écris {consignes[type_]} (réponds à ce message).",
                                            reply_markup=ForceReply(selective=True, input_field_placeholder="Ton élément…"))
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        amis.definir_reglage(session, f"ajout:{envoye.message_id}", f"{personne.id}:{type_}")


async def reponse_ajout(update: Update, context: ContextTypes.DEFAULT_TYPE, reponse_a: int) -> bool:
    """True si ce texte répondait à une demande d'élément (et a donc été pris en charge)."""
    with SessionLocal() as session:
        attente = amis.reglage(session, f"ajout:{reponse_a}")
        personne = acces.personne_connue(session, update.effective_user.id)
    if attente is None or int(attente.split(":")[0]) != personne.id:
        return False
    type_ = attente.split(":")[1]
    reponse = await asyncio.to_thread(_avec_session, defis.ajouter_element, personne, type_, update.message.text,
                                      ia.economique if type_ == "gage" else None)
    if reponse.startswith("✅"):
        with SessionLocal() as session:
            from .models import Reglage
            session.delete(session.get(Reglage, f"ajout:{reponse_a}"))
            session.commit()
    await update.message.reply_text(reponse)
    return True


async def commande_mesajouts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_avec_session(defis.mes_elements, _moi(update)))


async def commande_retirer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        numero = int(context.args[0].lstrip("#"))
    except (IndexError, ValueError):
        await update.message.reply_text("Format : /retirer 4")
        return
    await update.message.reply_text(_avec_session(defis.retirer_element, _moi(update), numero))


async def commande_verdict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_avec_session(defis.verdict, " ".join(context.args)))


async def commande_resultat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reponse = (context.args[0].lower() if context.args else "")
    if reponse not in ("oui", "non"):
        await update.message.reply_text("Format : /resultat oui, ou /resultat non")
        return
    texte = _avec_session(defis.resultat_pronostic, reponse == "oui")
    with SessionLocal() as session:
        groupe = amis.groupe_id(session)
    await context.bot.send_message(groupe or update.effective_chat.id, texte)


async def commande_imitation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texte = _avec_session(defis.reveler_imitation)
    with SessionLocal() as session:
        groupe = amis.groupe_id(session)
    await context.bot.send_message(groupe or update.effective_chat.id, texte)


def installer(app) -> None:
    for nom, fonction in (("idee", commande_idee), ("suggestion", commande_suggestion), ("pari", commande_pari),
                          ("paris", commande_paris), ("resolu", commande_resolu), ("course", commande_course),
                          ("defi", commande_defi), ("ajout", commande_ajout), ("mesajouts", commande_mesajouts),
                          ("retirer", commande_retirer), ("verdict", commande_verdict), ("resultat", commande_resultat),
                          ("imitation", commande_imitation)):
        app.add_handler(CommandHandler(nom, fonction))
    app.add_handler(CallbackQueryHandler(bouton_lancer, pattern=r"^df\|"))
    app.add_handler(CallbackQueryHandler(bouton_vote, pattern=r"^d\|"))
    app.add_handler(CallbackQueryHandler(bouton_ajout, pattern=r"^a\|"))
