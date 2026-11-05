"""Accueil des amis : /moment, /chute, /citation, médias, note du soir et capsule temporelle.

Cœur indépendant de Telegram : il lit et écrit la base et renvoie des textes ou des messages abstraits."""
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .horloge import maintenant, vers_paris
from .models import Capsule, Chute, Citation, Journee, Media, Moment, Note, Personne, Presence, Rappel, Reglage, Weekend
from .rappels import Message, _jour_lisible

TAILLE_MAX_MEDIA = 20 * 1024 * 1024  # limite de téléchargement des bots Telegram
HEURE_NOTE = time(20, 0)


def _maintenant_aware() -> datetime:
    return maintenant().replace(tzinfo=timezone.utc)


def aujourdhui() -> date:
    return vers_paris(maintenant()).date()


# --- Réglages ----------------------------------------------------------------

def reglage(session: Session, cle: str) -> str | None:
    r = session.get(Reglage, cle)
    return r.valeur if r else None


def definir_reglage(session: Session, cle: str, valeur: str) -> None:
    r = session.get(Reglage, cle)
    if r is None:
        session.add(Reglage(cle=cle, valeur=valeur))
    else:
        r.valeur = valeur
    session.commit()


def groupe_id(session: Session) -> int | None:
    v = reglage(session, "groupe_id")
    return int(v) if v else None


def nom_krok(personne: Personne) -> str:
    """Le nom que Krok emploie pour parler de cette personne : son surnom si l'organisateur lui en a donné un, sinon son prénom."""
    return personne.surnom or personne.prenom


def trouver_personne(session: Session, mot: str) -> Personne | None:
    """Une personne désignée par son prénom ou son surnom, sans tenir compte des majuscules."""
    mot = mot.strip().lower()
    return session.scalar(select(Personne).where(func.lower(Personne.prenom) == mot)) or \
        session.scalar(select(Personne).where(func.lower(Personne.surnom) == mot))


def definir_surnom(session: Session, prenom: str, surnom: str | None) -> str:
    """Donne (ou retire, avec surnom=None) le surnom d'une personne. Réservé à l'organisateur par la table des règles."""
    personne = session.scalar(select(Personne).where(func.lower(Personne.prenom) == prenom.strip().lower()))
    if personne is None:
        return f"Je ne connais pas « {prenom} »."
    if surnom is None:
        personne.surnom = None
        session.commit()
        return f"🧽 Surnom retiré : Krok appellera de nouveau {personne.prenom} par son prénom."
    surnom = surnom.strip()[:50]
    autre = session.scalar(select(Personne).where(func.lower(Personne.prenom) == surnom.lower()))
    if autre is not None and autre.id != personne.id:
        return f"« {surnom} » est le prénom de {autre.prenom} : choisis un autre surnom."
    personne.surnom = surnom
    session.commit()
    return f"✅ À partir de maintenant, Krok appelle {personne.prenom} « {surnom} »."


def lister_surnoms(session: Session) -> str:
    lignes = [f"{p.prenom} → {p.surnom or '(aucun)'}" for p in session.scalars(select(Personne).where(Personne.actif.is_(True)).order_by(Personne.id))]
    return "🏷 Surnoms (/surnom Prénom Surnom, ou /surnom Prénom - pour le retirer) :\n" + "\n".join(lignes)


def groupe_autorise(session: Session, chat_id: int) -> bool:
    """Le groupe de la série. Tant que /groupe n'a pas été tapé, aucun n'est enregistré : on n'écarte alors aucun groupe."""
    enregistre = groupe_id(session)
    return enregistre is None or chat_id == enregistre


# --- Journée du jour ----------------------------------------------------------

def _presence(session: Session, journee: Journee, personne: Personne) -> None:
    if session.scalar(select(Presence).where(Presence.journee_id == journee.id, Presence.personne_id == personne.id)) is None:
        session.add(Presence(journee_id=journee.id, personne_id=personne.id))


def journee_du_jour(session: Session, personne: Personne, jour: date | None = None) -> Journee:
    """La journée de ski d'aujourd'hui (ou du `jour` donné), créée au besoin : agir dans le bot vaut présence."""
    jour = jour or aujourdhui()
    journee = session.scalar(select(Journee).where(Journee.jour == jour))
    if journee is None:
        weekend = session.scalar(select(Weekend).where(Weekend.date_debut <= jour, Weekend.date_fin >= jour,
                                                       Weekend.statut != "annule"))
        if weekend is None:
            weekend = Weekend(date_debut=jour, date_fin=jour, statut="confirme")
            session.add(weekend)
            session.flush()
        journee = Journee(weekend_id=weekend.id, jour=jour)
        session.add(journee)
        session.flush()
    journee.ski = True
    _presence(session, journee, personne)
    session.commit()
    return journee


def personne_par_prenom(session: Session, prenom: str) -> Personne | None:
    return session.scalar(select(Personne).where(func.lower(Personne.prenom) == prenom.strip().lower()))


# --- /moment, /chute, /citation -----------------------------------------------

def ajouter_moment(session: Session, auteur: Personne, texte: str) -> str:
    journee = journee_du_jour(session, auteur)
    session.add(Moment(journee_id=journee.id, personne_id=auteur.id, texte=texte or None, cree_a=_maintenant_aware()))
    session.commit()
    heure = vers_paris(maintenant()).strftime("%H:%M")
    return f"📍 Moment noté à {heure}" + (f" : {texte}" if texte else ".")


def ajouter_chute(session: Session, auteur: Personne, cible: str | None, verte: bool = False) -> str:
    personne = auteur
    if cible:
        personne = personne_par_prenom(session, cible)
        if personne is None:
            noms = ", ".join(p.prenom for p in session.scalars(select(Personne).order_by(Personne.id)))
            return f"Je ne connais pas « {cible} ». Essaie : {noms}."
    journee = journee_du_jour(session, personne)
    session.add(Chute(journee_id=journee.id, personne_id=personne.id, verte=verte, cree_a=_maintenant_aware()))
    session.commit()
    jour = session.scalar(select(func.count()).select_from(Chute).where(Chute.journee_id == journee.id, Chute.personne_id == personne.id))
    saison = session.scalar(select(func.count()).select_from(Chute).where(Chute.personne_id == personne.id))
    return f"🤕 Chute de {personne.prenom} notée{' sur une verte 🟢' if verte else ''}. Aujourd'hui : {jour} · saison : {saison}."


def ajouter_citation(session: Session, auteur: Personne, args: list[str]) -> str:
    """/citation Adrien « texte » ; sans prénom, la citation est celle de l'auteur du message."""
    personne, mots = auteur, list(args)
    if mots and personne_par_prenom(session, mots[0]) is not None:
        personne, mots = personne_par_prenom(session, mots[0]), mots[1:]
    texte = " ".join(mots).strip().strip('"«»“”').strip()
    if not texte:
        return "Format : /citation Adrien \"la phrase culte\""
    journee = journee_du_jour(session, auteur)
    session.add(Citation(journee_id=journee.id, personne_id=personne.id, texte=texte, cree_a=_maintenant_aware()))
    session.commit()
    return f"💬 Citation de {personne.prenom} notée : « {texte} »"


# --- Médias ------------------------------------------------------------------------

def ranger_media(session: Session, auteur: Personne, type_: str, file_id: str, taille: int | None, legende: str | None) -> str:
    """Range la référence d'une photo ou d'une vidéo dans la journée d'aujourd'hui, si elle existe."""
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui()))
    session.add(Media(personne_id=auteur.id, journee_id=journee.id if journee else None, type=type_, file_id=file_id,
                      taille=taille, legende=legende or None, cree_a=_maintenant_aware()))
    session.commit()
    gros = " Plus de 20 Mo : le bot ne peut pas le télécharger lui-même, mais /medias te le renverra en taille réelle." if taille and taille > TAILLE_MAX_MEDIA else ""
    if journee is None:
        return "📎 Reçu et gardé (hors sortie : aucune journée de ski aujourd'hui)." + gros
    return f"📎 Rangé dans la journée du {_jour_lisible(journee.jour)}." + gros


# --- Note du soir ----------------------------------------------------------------------

def planifier_notes(session: Session, forcer: bool = False) -> None:
    """À 20 h, prévoit pour chaque présent de la journée un message pour sa note sur 10 et sa phrase."""
    if not forcer and vers_paris(maintenant()).time() < HEURE_NOTE:
        return
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui(), Journee.ski.is_(True)))
    if journee is None:
        return
    for presence in journee.presences:
        personne = session.get(Personne, presence.personne_id)
        deja = session.scalar(select(Rappel).where(Rappel.type == "note", Rappel.journee_id == journee.id,
                                                   Rappel.destinataire_id == personne.id))
        if personne.telegram_id and deja is None:
            session.add(Rappel(destinataire_id=personne.id, type="note", journee_id=journee.id, envoyer_a=maintenant()))
    session.commit()


def ecran_note(journee_id: int) -> Message:
    boutons = [[(str(k), f"n|{journee_id}|{k}") for k in range(1, 6)], [(str(k), f"n|{journee_id}|{k}") for k in range(6, 11)]]
    return Message("⭐ Ta note sur 10 pour aujourd'hui : plutôt peur ou confiance, c'est toi qui vois. "
                   "1 = tu n'en menais pas large, 10 = tu volais.", boutons)


def enregistrer_note(session: Session, personne: Personne, journee_id: int, valeur: int) -> Note:
    note = session.scalar(select(Note).where(Note.journee_id == journee_id, Note.personne_id == personne.id))
    if note is None:
        note = Note(journee_id=journee_id, personne_id=personne.id, note=valeur)
        session.add(note)
    else:
        note.note = valeur
    session.commit()
    return note


def enregistrer_phrase(session: Session, personne: Personne, reponse_a: int, texte: str) -> str | None:
    """None si ce message ne répond à aucune demande de phrase de cette personne."""
    note = session.scalar(select(Note).where(Note.message_id == reponse_a, Note.personne_id == personne.id))
    if note is None:
        return None
    note.phrase = texte.strip()
    session.commit()
    return f"✅ Notée : {note.note}/10 — « {note.phrase} »"


# --- Capsule temporelle -------------------------------------------------------------------

QUESTIONS_CAPSULE = [("prediction", "🔮 Capsule temporelle : ta prédiction pour la saison ? (Réponds à ce message)"),
                     ("objectif", "🎯 Ton objectif pour la saison ?"),
                     ("message", "💌 Un message à toi-même, qu'on te lira au dernier week-end de la saison ?")]


def _prochaine_question(capsule: Capsule) -> tuple[str, str] | None:
    return next(((champ, texte) for champ, texte in QUESTIONS_CAPSULE if not getattr(capsule, champ)), None)


def demarrer_capsule(session: Session, personne: Personne) -> str | None:
    """Texte de la prochaine question de la capsule, ou None si elle est déjà scellée."""
    capsule = session.scalar(select(Capsule).where(Capsule.personne_id == personne.id))
    if capsule is None:
        capsule = Capsule(personne_id=personne.id)
        session.add(capsule)
        session.commit()
    suite = _prochaine_question(capsule)
    return suite[1] if suite else None


def enregistrer_message_capsule(session: Session, personne: Personne, message_id: int) -> None:
    capsule = session.scalar(select(Capsule).where(Capsule.personne_id == personne.id))
    capsule.message_id = message_id
    session.commit()


def repondre_capsule(session: Session, personne: Personne, reponse_a: int, texte: str) -> tuple[str, str | None] | None:
    """Range la réponse. Renvoie (confirmation, question suivante ou None) ; None si ce n'est pas une réponse de capsule."""
    capsule = session.scalar(select(Capsule).where(Capsule.personne_id == personne.id, Capsule.message_id == reponse_a))
    if capsule is None:
        return None
    champ, _ = _prochaine_question(capsule)
    setattr(capsule, champ, texte.strip())
    capsule.message_id = None
    session.commit()
    suite = _prochaine_question(capsule)
    if suite:
        return "👌 Noté.", suite[1]
    return ("🔒 Capsule scellée. Le bot ne la montrera à personne avant le dernier week-end de la saison, "
            "même pas à Maxime."), None


def reveler_capsules(session: Session) -> list[str]:
    """Une capsule scellée et complète par message, confrontée aux stats ; chaque capsule n'est révélée qu'une fois."""
    textes = []
    for capsule in session.scalars(select(Capsule).where(Capsule.revelee_a.is_(None)).order_by(Capsule.id)):
        if not (capsule.prediction and capsule.objectif and capsule.message):
            continue
        personne = session.get(Personne, capsule.personne_id)
        jours = session.scalar(select(func.count()).select_from(Presence).join(Journee).where(
            Presence.personne_id == personne.id, Journee.ski.is_(True)))
        chutes = session.scalar(select(func.count()).select_from(Chute).where(Chute.personne_id == personne.id))
        textes.append(f"🔓 Capsule de {personne.prenom}\n\n🔮 Prédiction : {capsule.prediction}\n🎯 Objectif : {capsule.objectif}\n"
                      f"💌 Message : {capsule.message}\n\n📊 Bilan de la saison : {jours} jours de ski, {chutes} chutes.")
        capsule.revelee_a = _maintenant_aware()
    session.commit()
    return textes
