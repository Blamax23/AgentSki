"""Teste l'accueil des amis : marqueurs, chutes, citations, médias, note du soir, capsule, groupe.
Cœur et couche Telegram avec de faux messages, base jetable, horloge simulée.
Lancer : .venv/bin/python scripts/test_amis.py"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "amis.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, commandes, horloge, rappels  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Capsule, Chute, Citation, Journee, Media, Moment, Note, Personne, Presence, Rappel  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    adrien.telegram_id = 2
    s.commit()


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(heure, jour=12):
    horloge.regler(horloge.vers_utc(datetime(2026, 12, jour, *heure)))


def p(session, prenom):
    return session.scalar(select(Personne).where(Personne.prenom == prenom))


# --- Cœur ---------------------------------------------------------------------
a((14, 30))
with SessionLocal() as s:
    maxime, adr, achille = p(s, "Maxime"), p(s, "Adrien"), p(s, "Achille")
    rep = amis.ajouter_moment(s, maxime, "Première trace sur la Timale")
    verifie("14:30" in rep and "Timale" in rep, "/moment : marqueur horodaté à l'heure de Paris")
    verifie("📍" in amis.ajouter_moment(s, maxime, ""), "/moment sans texte : marqueur seul")
    j = s.scalar(select(Journee).where(Journee.jour == date(2026, 12, 12)))
    verifie(j is not None and j.ski, "agir crée la journée du jour si elle n'existe pas")
    verifie(len(s.scalars(select(Moment).where(Moment.journee_id == j.id)).all()) == 2, "deux marqueurs rangés")

    rep = amis.ajouter_chute(s, maxime, None)
    verifie("Maxime" in rep and "Aujourd'hui : 1" in rep and "saison : 1" in rep, "/chute : la sienne par défaut, avec les totaux")
    rep = amis.ajouter_chute(s, maxime, "adrien")
    verifie("Adrien" in rep, "/chute adrien : insensible à la casse")
    pres = {s.get(Personne, x.personne_id).prenom for x in s.scalars(select(Presence).where(Presence.journee_id == j.id))}
    verifie(pres == {"Maxime", "Adrien"}, "une chute de quelqu'un vaut présence de cette personne")
    verifie("Je ne connais pas" in amis.ajouter_chute(s, maxime, "Zébulon"), "/chute d'un inconnu : message clair")
    verifie(s.scalar(select(Chute.id).where(Chute.personne_id == achille.id)) is None, "…et rien n'est écrit")

    rep = amis.ajouter_citation(s, maxime, ["Adrien", '"C\'est', 'une', 'verte', 'non', '?"'])
    verifie("Adrien" in rep and "C'est une verte non ?" in rep, "/citation Adrien \"texte\"")
    verifie(s.scalar(select(Citation)).texte == "C'est une verte non ?", "guillemets retirés")
    rep = amis.ajouter_citation(s, adr, ["Je", "freine", "pas"])
    verifie("Adrien" in rep, "/citation sans prénom : celle de l'auteur")
    verifie("Format" in amis.ajouter_citation(s, adr, []), "/citation vide : le format est rappelé")

    verifie("/medias" in amis.ranger_media(s, adr, "video", "F1", 25 * 1024 * 1024, None), "média de plus de 20 Mo gardé, avec la façon de le récupérer")
    verifie("Rangé dans la journée du Samedi 12 décembre" in amis.ranger_media(s, adr, "photo", "F2", 80_000, "vue"), "photo rangée dans la journée")
    verifie(s.scalar(select(Media).where(Media.file_id == "F2")).legende == "vue", "seule la référence et la légende sont gardées")
    verifie(s.scalar(select(Media.id).where(Media.file_id == "F1")) is not None, "le fichier de plus de 20 Mo est bien rangé aussi")
    a((14, 30), 20)  # un autre jour, sans journée de ski
    verifie("hors sortie" in amis.ranger_media(s, adr, "photo", "F3", 1000, None), "sans journée de ski : gardé hors sortie")
    verifie(s.scalar(select(Media).where(Media.file_id == "F3")).journee_id is None, "…sans journée rattachée")

# --- Note du soir ------------------------------------------------------------------
a((19, 59))
with SessionLocal() as s:
    amis.planifier_notes(s)
    verifie(s.scalar(select(Rappel.id).where(Rappel.type == "note")) is None, "avant 20 h : aucune demande de note")
    a((20, 0))
    amis.planifier_notes(s)
    notes_prevues = s.scalars(select(Rappel).where(Rappel.type == "note")).all()
    verifie({s.get(Personne, r.destinataire_id).prenom for r in notes_prevues} == {"Maxime", "Adrien"}, "à 20 h : une demande pour chaque présent qui a un compte")
    amis.planifier_notes(s)
    verifie(len(s.scalars(select(Rappel).where(Rappel.type == "note")).all()) == 2, "pas de doublon à la minute suivante")
    envois = rappels.a_envoyer(s)
    ecran = next(x.message for x in envois if "Ta note sur 10" in x.message.texte)
    verifie(len([d for ligne in ecran.boutons for _, d in ligne]) == 10, "dix boutons, de 1 à 10")
    j = s.scalar(select(Journee).where(Journee.jour == date(2026, 12, 12)))
    note = amis.enregistrer_note(s, p(s, "Adrien"), j.id, 7)
    note.message_id = 555
    s.commit()
    amis.enregistrer_note(s, p(s, "Adrien"), j.id, 8)
    verifie(s.scalar(select(Note)).note == 8 and len(s.scalars(select(Note)).all()) == 1, "changer sa note remplace la précédente")
    verifie(amis.enregistrer_phrase(s, p(s, "Maxime"), 555, "x") is None, "la phrase d'un autre n'est pas prise pour la sienne")
    verifie(amis.enregistrer_phrase(s, p(s, "Adrien"), 999, "x") is None, "un texte qui ne répond pas à la demande n'est pas une phrase")
    verifie("8/10" in amis.enregistrer_phrase(s, p(s, "Adrien"), 555, "J'ai pas freiné"), "la phrase en réponse à la demande est rangée")

# --- Capsule ----------------------------------------------------------------------------
with SessionLocal() as s:
    adr = p(s, "Adrien")
    q = amis.demarrer_capsule(s, adr)
    verifie("prédiction" in q, "capsule : première question")
    amis.enregistrer_message_capsule(s, adr, 600)
    verifie(amis.repondre_capsule(s, adr, 601, "x") is None, "une réponse à un autre message n'est pas la capsule")
    conf, suite = amis.repondre_capsule(s, adr, 600, "Je tombe 30 fois")
    verifie("objectif" in suite, "puis l'objectif")
    amis.enregistrer_message_capsule(s, adr, 601)
    conf, suite = amis.repondre_capsule(s, adr, 601, "Faire une noire")
    amis.enregistrer_message_capsule(s, adr, 602)
    conf, suite = amis.repondre_capsule(s, adr, 602, "Tiens bon")
    verifie("scellée" in conf and suite is None, "trois réponses : capsule scellée")
    verifie(amis.demarrer_capsule(s, adr) is None, "une capsule scellée ne se redemande pas")
    incomplete = amis.demarrer_capsule(s, p(s, "Maxime"))
    textes = amis.reveler_capsules(s)
    verifie(len(textes) == 1 and "Je tombe 30 fois" in textes[0] and "chutes" in textes[0], "révélation : la capsule complète, confrontée aux stats")
    verifie(amis.reveler_capsules(s) == [], "une capsule n'est révélée qu'une fois")
    verifie("Maxime" not in textes[0], "la capsule incomplète de Maxime n'est pas révélée")

# --- Couche Telegram ------------------------------------------------------------------------
def maj(uid, texte=None, args=(), chat="private", reponse_a=None, **extra):
    msg = SimpleNamespace(text=texte, reply_text=AsyncMock(), reply_to_message=SimpleNamespace(message_id=reponse_a) if reponse_a else None,
                          caption=extra.get("caption"), photo=extra.get("photo"), video=None, animation=None, document=None)
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=uid, first_name="T"),
                           effective_chat=SimpleNamespace(id=77, type=chat)), SimpleNamespace(args=list(args), bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900))))


async def telegram():
    up, ctx = maj(2, args=["Beau", "virage"])
    await commandes.commande_moment(up, ctx)
    verifie("Beau virage" in up.message.reply_text.call_args[0][0], "/moment depuis Telegram")

    up, ctx = maj(1, chat="group")
    await commandes.commande_groupe(up, ctx)
    with SessionLocal() as s:
        verifie(amis.groupe_id(s) == 77, "/groupe dans un groupe : le groupe est enregistré")
    up, ctx = maj(2, chat="group")
    up.effective_chat.id = 88
    await commandes.commande_groupe(up, ctx)
    with SessionLocal() as s:
        verifie(amis.groupe_id(s) == 77, "/groupe d'un ami : ignoré")
    up, ctx = maj(1, chat="private")
    await commandes.commande_groupe(up, ctx)
    verifie("dans le groupe" in up.message.reply_text.call_args[0][0], "/groupe en privé : explication")

    up, ctx = maj(2, photo=[SimpleNamespace(file_id="P1", file_size=1000), SimpleNamespace(file_id="P2", file_size=5000)], caption="Vue")
    await commandes.media(up, ctx)
    with SessionLocal() as s:
        verifie(s.scalar(select(Media).where(Media.file_id == "P2")) is not None, "photo : la plus grande taille est rangée")

    up, ctx = maj(2, "Une phrase")
    with SessionLocal() as s:
        note = s.scalar(select(Note))
        note.message_id, note.phrase = 700, None
        s.commit()
    up, ctx = maj(2, "Faire une noire cette saison", reponse_a=700)
    await commandes.texte_libre(up, ctx)
    verifie("Notée" in up.message.reply_text.call_args[0][0], "réponse forcée à la demande de phrase")

    with SessionLocal() as s:
        achille = p(s, "Achille")
        achille.telegram_id = 3
        s.commit()
        amis.demarrer_capsule(s, achille)
        amis.enregistrer_message_capsule(s, achille, 710)
    up, ctx = maj(3, "Ma prédiction", reponse_a=710)
    await commandes.texte_libre(up, ctx)
    verifie("Noté" in up.message.reply_text.call_args[0][0] and ctx.bot.send_message.await_count == 1, "capsule : réponse reconnue, question suivante posée")

    up, ctx = maj(1, chat="private")
    await commandes.commande_revelation(up, ctx)
    verifie("Aucune capsule complète" in up.message.reply_text.call_args[0][0], "/revelation sans capsule complète")
    up, ctx = maj(2, chat="private")
    await commandes.commande_revelation(up, ctx)
    verifie(not up.message.reply_text.await_count, "/revelation d'un ami : ignoré")

    up, ctx = maj(1)
    await commandes.start(up, ctx)
    verifie("/moment" in up.message.reply_text.call_args[0][0], "/start : l'aide des commandes")

asyncio.run(telegram())
print("\nTout passe.")
