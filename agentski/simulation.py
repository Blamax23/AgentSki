"""Fausses commandes pour simuler ce qui n'est pas encore automatisé. Tout ce fichier disparaît au gel de l'étape 4.

/jeudi            pose la question du jeudi tout de suite
/horloge [date]   fixe l'heure du bot (« 2026-12-12 07:30 », heure de Paris) ; sans argument, revient à l'heure réelle
/simule_rappels   liste les rappels en attente
/simule_pov       « /simule_pov Adrien 5 » : ajoute des descentes filmées (ce que fera l'entretien du soir, étape 3)
/simule_reset     vide week-ends, présences, descentes, rappels, entretiens et faits du journal (avec confirmation)
/simule_entretien démarre l'entretien du soir maintenant (ce que fera le bouton du rappel de 19 h) ; crée la journée du jour si besoin
/simule_notes     envoie tout de suite la demande de note sur 10 et de phrase (ce qui part à 20 h) aux présents du jour
/simule_avalanche N  fixe un faux niveau de risque d'avalanche de 1 à 5 (le vrai bulletin n'est pas branché) ; sans N, l'efface
/simule_vocal     « /simule_vocal Samedi à Flaine avec Adrien… » : un vocal dont tu fournis la transcription
"""
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import acces, config, entretien, horloge, rappels
from .db import SessionLocal
from .models import (ChampJournal, Chute, Citation, Descente, Entretien, Journee, Moment, Personne, Presence, Rappel,
                     Sequence, Transcription, Weekend)


def reserve(handler):
    """Ces commandes modifient la base : réservées à l'organisateur, pas aux amis qui rejoindront le bot."""
    async def enveloppe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        with SessionLocal() as session:
            personne = acces.personne_connue(session, update.effective_user.id)
        if personne is None or personne.prenom != config.ORGANISATEUR:
            return
        await handler(update, context)
    return enveloppe


async def jeudi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .bot import clavier
    message = rappels.ecran_jours(rappels.jeudi_courant(), 0)
    await update.message.reply_text(message.texte, reply_markup=clavier(message))


async def commande_horloge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .bot import tic
    if not context.args:
        horloge.regler(None)
        await update.message.reply_text(f"Horloge réelle rétablie : {horloge.vers_paris(horloge.maintenant()):%a %d/%m %H:%M}.")
        return
    try:
        local = datetime.strptime(" ".join(context.args), "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Format : /horloge 2026-12-12 07:30 (heure de Paris), ou /horloge seul pour revenir à l'heure réelle.")
        return
    horloge.regler(horloge.vers_utc(local))
    await update.message.reply_text(f"⏱ Horloge figée au {local:%a %d/%m %H:%M}. J'exécute la boucle des rappels maintenant. "
                                    "/horloge seul pour revenir à l'heure réelle.")
    await tic(context)


async def commande_rappels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        lignes = [f"{horloge.vers_paris(r.envoyer_a):%a %d/%m %H:%M} — {r.type}" for r in session.scalars(
            select(Rappel).where(Rappel.envoye_a.is_(None), Rappel.abandonne_a.is_(None)).order_by(Rappel.envoyer_a))]
    maintenant = horloge.vers_paris(horloge.maintenant())
    await update.message.reply_text(f"Il est {maintenant:%a %d/%m %H:%M}. Rappels en attente :\n" + ("\n".join(lignes) or "aucun"))


async def commande_pov(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        prenom, n = context.args[0], int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Format : /simule_pov Adrien 5")
        return
    with SessionLocal() as session:
        personne = session.scalar(select(Personne).where(Personne.prenom.ilike(prenom)))
        journee = session.scalar(select(Journee).order_by(Journee.jour.desc()))
        if personne is None or journee is None:
            await update.message.reply_text("Personne inconnue, ou aucune journée : réponds d'abord à /jeudi.")
            return
        sequence = session.scalar(select(Sequence).where(Sequence.journee_id == journee.id))
        if sequence is None:
            sequence = Sequence(journee_id=journee.id, lieu="simulation")
            session.add(sequence)
            session.flush()
        session.add_all(Descente(sequence_id=sequence.id, filmeur_id=personne.id) for _ in range(n))
        session.commit()
    await update.message.reply_text(f"{n} descentes filmées par {personne.prenom} ajoutées (simulation).")


async def commande_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Vider week-ends, journées, présences, descentes, rappels, entretiens, chutes et faits du journal ? "
        "Les personnes et leurs comptes ne sont pas touchés.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Oui, vider", callback_data="sim|reset"),
                                            InlineKeyboardButton("Annuler", callback_data="sim|annule")]]))


async def bouton_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "sim|reset":
        with SessionLocal() as session:
            for table in (ChampJournal, Transcription, Entretien, Chute, Moment, Citation, Rappel, Descente, Sequence,
                          Presence, Journee, Weekend):
                session.execute(delete(table))
            session.commit()
        await query.edit_message_text("🧹 Fait : plus aucun week-end, rappel ni entretien en base.")
    else:
        await query.edit_message_text("Annulé.")


async def commande_entretien(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .bot import envoyer
    aujourdhui = horloge.vers_paris(horloge.maintenant()).date()
    with SessionLocal() as session:
        orga = acces.personne_connue(session, update.effective_user.id)
        journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui))
        if journee is None:  # simulation : une journée de ski aujourd'hui, avec Adrien
            weekend = Weekend(date_debut=aujourdhui, date_fin=aujourdhui, statut="confirme")
            session.add(weekend)
            session.flush()
            journee = Journee(weekend_id=weekend.id, jour=aujourdhui)
            session.add(journee)
            session.flush()
            adrien = session.scalar(select(Personne).where(Personne.prenom == "Adrien"))
            session.add_all(Presence(journee_id=journee.id, personne_id=p.id) for p in (orga, adrien))
            session.commit()
        messages = entretien.demarrer(session, journee.id, orga.id)
    await envoyer(update.effective_chat.id, context, messages)


async def commande_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .bot import tic
    from . import amis
    with SessionLocal() as session:
        orga = acces.personne_connue(session, update.effective_user.id)
        amis.journee_du_jour(session, orga)  # crée la journée du jour si besoin
        amis.planifier_notes(session, forcer=True)
    await tic(context)
    await update.message.reply_text("Demande de note envoyée aux présents du jour qui ont un compte Telegram.")


async def commande_avalanche_simulee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from . import amis, briefing
    from .models import Reglage
    with SessionLocal() as session:
        if not context.args:
            r = session.get(Reglage, "avalanche_simule")
            if r is not None:
                session.delete(r)
                session.commit()
            await update.message.reply_text("Niveau simulé effacé.")
            return
        if context.args[0] not in ("1", "2", "3", "4", "5"):
            await update.message.reply_text("Format : /simule_avalanche 3 (de 1 à 5)")
            return
        amis.definir_reglage(session, "avalanche_simule", context.args[0])
        await update.message.reply_text(briefing.texte_avalanche(session))


async def commande_vocal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .bot import traiter_transcription
    texte = " ".join(context.args)
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        e = entretien.entretien_ouvert(session, personne.id)
        entretien_id = e.id if e else None
    if entretien_id is None or not texte:
        await update.message.reply_text("Format : /simule_vocal <ce que tu dirais>, pendant un entretien (/simule_entretien).")
        return
    await traiter_transcription(update.effective_chat.id, context, entretien_id, texte)


def installer(app) -> None:
    app.add_handler(CommandHandler("jeudi", reserve(jeudi)))
    app.add_handler(CommandHandler("horloge", reserve(commande_horloge)))
    app.add_handler(CommandHandler("simule_rappels", reserve(commande_rappels)))
    app.add_handler(CommandHandler("simule_pov", reserve(commande_pov)))
    app.add_handler(CommandHandler("simule_reset", reserve(commande_reset)))
    app.add_handler(CommandHandler("simule_entretien", reserve(commande_entretien)))
    app.add_handler(CommandHandler("simule_vocal", reserve(commande_vocal)))
    app.add_handler(CommandHandler("simule_notes", reserve(commande_notes)))
    app.add_handler(CommandHandler("simule_avalanche", reserve(commande_avalanche_simulee)))
    app.add_handler(CallbackQueryHandler(reserve(bouton_reset), pattern=r"^sim\|"))
