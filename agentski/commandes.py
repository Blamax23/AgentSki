"""Commandes Telegram de l'accueil des amis : /start, /groupe, /moment, /chute, /citation, médias, note du soir, capsule."""
from telegram import ForceReply, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import acces, amis, config
from .db import SessionLocal

AIDE = """Voilà ce que je sais faire :

🎿 Pendant le week-end
/moment [texte] : poser un marqueur
/chute [prénom] [verte] : noter une chute
/citation [prénom] "phrase" : garder une phrase culte
/chrono piste 1:32 : noter un chrono
/gopro : à qui le tour de filmer
/ou [prénom] : où est quelqu'un
/rdv : un point de rendez-vous
/position : partager ta position en direct
/confessionnal : le plan de la soirée (organisateur)

🌤 Météo et neige
/info [station] : les conditions
/meteo [station] [jour] : les prévisions
/avalanche : le risque d'avalanche
/pointmeteo on 07:00 : point météo chaque matin

🎵 Musique
/son [titre ou lien] : ajouter à la playlist
/play [titre] : lancer sur l'appareil Spotify actif
/file [titre] : mettre en file d'attente
/pause : mettre en pause

🎲 Jeux et défis
/defi : lancer un défi
/duel : un duel entre deux personnes
/quicestce : qui a dit cette phrase ?
/pari [texte] : lancer un pari
/paris : les paris en cours
/fils : paris et défis en cours
/trophees : tes trophées

💡 Idées et organisation
/idee [texte] : la boîte à idées
/suggestion [thème] : des idées trouvées sur le web
/course [plat] : la liste de courses
/ics : les sorties dans ton agenda
/afaire : les pistes pas encore faites

📊 Chiffres et souvenirs
/stats [prénom|tous] : tes statistiques
/journal : relire un week-end
/kit : exporter ce qui a été vécu
/medias [hier|date|tout] : renvoyer les photos, vidéos et vocaux gardés (organisateur)
/episode lien titre : enregistrer un épisode
/vues : les chiffres YouTube
/capsule : remplir ta capsule temporelle

😄 Surprises
/easter : ajouter une archive (en privé)
/chambre [prénom] : une archive tout de suite
/ajout : alimenter les réserves (en privé)
/preaud : le classement des Preaud
/icham [phrase] : traduite en arabe
/adrille_rallye : une pub

🤖 Krok
/talk [message] : discuter (ou écris-moi directement)
/achille : Krok parle comme Achille
/oublie : effacer ce que Krok sait de toi (en privé)

📎 Sans commande
Envoie-moi tes photos et vidéos de moins de 20 Mo : je les range par journée.
Envoie-moi des vocaux quand l'envie te prend : je les garde pour la série.
Envoie-moi un fichier .fit ou .gpx de ta montre : j'en tire tes descentes.
Le soir, je te demande ta note sur 10 et une phrase."""


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


async def commande_medias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Renvoie les médias gardés (n'importe quelle taille) dans la conversation, pour les télécharger depuis Telegram."""
    import asyncio
    from datetime import date as _date, timedelta
    from . import fun, medias
    jour, tout, page = None, False, 1
    for a in context.args:
        if a.lower() == "tout":
            tout = True
        elif a.lower() in ("hier", "aujourd'hui", "aujourdhui"):
            jour = amis.aujourdhui() - timedelta(days=1 if a.lower() == "hier" else 0)
        elif a.isdigit() and len(a) <= 3:
            page = max(1, int(a))
        else:
            try:
                jour = _date.fromisoformat(a)
            except ValueError:
                await update.message.reply_text("Format : /medias, /medias hier, /medias 2027-01-02, /medias tout, et un numéro de page à la fin.")
                return
    with SessionLocal() as session:
        liste, total, choisi = medias.a_envoyer(session, jour, tout, page)
    if not liste:
        await update.message.reply_text("Aucun média pour ça." if total == 0 else f"Il n'y a pas de page {page} : {total} médias en tout.")
        return
    pages = -(-total // medias.PAR_PAGE)
    await update.message.reply_text(f"📥 {total} média{'s' if total > 1 else ''}" + (f" du {choisi:%d/%m/%Y}" if choisi else "") + f", page {page}/{pages}. Je les envoie.")
    for type_, file_id, legende in liste:
        await fun.envoyer_media(context.bot, update.effective_chat.id, type_, file_id, legende)
        await asyncio.sleep(0.4)
    if page < pages:
        await update.message.reply_text(f"Il en reste : relance avec le numéro de page {page + 1} à la fin de la commande.")


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
        from . import fun, jeux
        if await fun.description_archive(update, context, reponse_a) or await jeux.reponse_ajout(update, context, reponse_a):
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
    app.add_handler(CommandHandler("medias", commande_medias))
    app.add_handler(CommandHandler("revelation", commande_revelation))
    app.add_handler(CallbackQueryHandler(bouton_note, pattern=r"^n\|"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.IMAGE
                                   | filters.Document.VIDEO, media))
