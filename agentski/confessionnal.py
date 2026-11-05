"""Le plan de confessionnal de la soirée : qui passe, dans quel ordre, avec quelle question. Le tournage se fait hors du bot ;
le plan est proposé à l'organisateur, qui dit librement s'il l'a suivi ou non. Le bot ne lit, ne résume ni ne retranscrit aucune réponse.
Cœur indépendant de Telegram, le hasard est injectable."""
import random
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .amis import aujourdhui, definir_reglage, reglage
from .horloge import maintenant, vers_paris, vers_utc
from .models import Confessionnal, Journee, Personne, Presence
from .rappels import Message, _heure, _jour_lisible, organisateur
from .surprises import Evenement

LIBRES = [
    "Le meilleur moment de ta journée.",
    "La descente que tu voudrais oublier.",
    "Un mot sur chacun des autres.",
    "Ce qui t'a surpris aujourd'hui.",
    "Le moment où tu as eu le plus froid.",
    "Une chose que tu referais sans hésiter.",
    "Ta note sur 10 de la journée, et pourquoi.",
    "Le plus gros fou rire du jour.",
    "Ce que tu n'avoueras qu'ici.",
    "Ta pire erreur du jour.",
    "Qui mérite le titre de MVP du jour, et pourquoi ?",
    "Ce que tu as mangé de mieux depuis le début.",
]
PREDICTION = "Ta prédiction pour demain : que va-t-il se passer ?"
VERDICT = "Hier, tu as fait ta prédiction pour aujourd'hui en confessionnal. Verdict : avais-tu raison ?"
DUOS = [
    "{a} et {b}, face à face : ce que vous admirez chacun chez l'autre, et ce qui vous agace un peu.",
    "{a} et {b} : lequel des deux craquera le premier demain, et pourquoi ?",
    "{a} et {b} : racontez un souvenir de ce week-end vu chacun de votre côté.",
]
RESOLUTION = "Ta résolution pour l'année."
BILAN = ["Ce que tu retiens de ce week-end.", "Le moment à ne surtout pas oublier.", "Une chose que tu feras différemment la prochaine fois."]

ICONES = {"propose": "⏳", "fait": "✅", "passe": "⏭"}


def _presents(session: Session, journee: Journee) -> list[Personne]:
    return list(session.scalars(select(Personne).join(Presence, Presence.personne_id == Personne.id).where(
        Presence.journee_id == journee.id).order_by(Personne.id)))


def _deja_posees(session: Session, personne: Personne) -> set[str]:
    return set(session.scalars(select(Confessionnal.question).where(Confessionnal.personne_id == personne.id)))


def _faits(session: Session, personne: Personne) -> int:
    return session.scalar(select(func.count()).select_from(Confessionnal).where(Confessionnal.personne_id == personne.id, Confessionnal.statut == "fait"))


def _libre(session: Session, personne: Personne, exclues: set[str], hasard) -> str:
    """Une question pas encore posée à cette personne ni utilisée ce soir ; si la banque est épuisée, on reprend."""
    neuves = [q for q in LIBRES if q not in _deja_posees(session, personne) and q not in exclues]
    return hasard.choice(neuves or [q for q in LIBRES if q not in exclues] or LIBRES)


def _prediction_hier(session: Session, personne: Personne, journee: Journee) -> bool:
    """Vrai si la personne a fait, la veille, le confessionnal de prédiction (donc elle doit un verdict aujourd'hui)."""
    hier = session.scalar(select(Journee).where(Journee.jour == journee.jour - timedelta(days=1), Journee.ski.is_(True)))
    return hier is not None and session.scalar(select(Confessionnal.id).where(
        Confessionnal.journee_id == hier.id, Confessionnal.personne_id == personne.id,
        Confessionnal.type == "prediction", Confessionnal.statut == "fait")) is not None


def _lendemain_ski(session: Session, journee: Journee) -> bool:
    return session.scalar(select(Journee.id).where(Journee.jour == journee.jour + timedelta(days=1), Journee.ski.is_(True))) is not None


def lignes(session: Session, journee: Journee) -> list[Confessionnal]:
    return list(session.scalars(select(Confessionnal).where(Confessionnal.journee_id == journee.id).order_by(Confessionnal.ordre)))


def proposer_plan(session: Session, journee: Journee, hasard=random) -> list[Confessionnal]:
    """Crée le plan de la soirée s'il n'existe pas : ceux qui ont le moins passé d'abord, une question chacun, parfois un duo à la fin."""
    existantes = lignes(session, journee)
    if existantes:
        return existantes
    presents = _presents(session, journee)
    hasard.shuffle(presents)  # l'égalité se joue au hasard...
    presents.sort(key=lambda p: _faits(session, p))  # ...le tri est stable : le moins passé jusqu'ici ouvre le plan
    est_derniere = not _lendemain_ski(session, journee)
    nouvel_an = (journee.jour.month, journee.jour.day) == (1, 1)
    utilisees: set[str] = set()
    rangees: list[Confessionnal] = []
    for p in presents:
        if _prediction_hier(session, p, journee):
            type_, question = "verdict", VERDICT
        elif nouvel_an:
            type_, question = "special", RESOLUTION
        elif est_derniere:
            type_, question = "special", hasard.choice([b for b in BILAN if b not in utilisees] or BILAN)
        elif hasard.random() < 0.5:
            type_, question = "prediction", PREDICTION
        else:
            type_, question = "libre", _libre(session, p, utilisees, hasard)
        utilisees.add(question)
        rangees.append(Confessionnal(journee_id=journee.id, ordre=len(rangees) + 1, personne_id=p.id, type=type_, question=question))
    if len(presents) >= 3 and hasard.random() < 0.5:
        a, b = hasard.sample(presents, 2)
        rangees.append(Confessionnal(journee_id=journee.id, ordre=len(rangees) + 1, personne_id=a.id, autre_id=b.id, type="duo",
                                     question=hasard.choice(DUOS).format(a=a.prenom, b=b.prenom)))
    session.add_all(rangees)
    session.commit()
    return rangees


def message(session: Session, journee: Journee) -> Message:
    rangees = lignes(session, journee)
    corps, boutons = [], []
    for c in rangees:
        qui = session.get(Personne, c.personne_id).prenom + (f" + {session.get(Personne, c.autre_id).prenom}" if c.autre_id else "")
        corps.append(f"{ICONES[c.statut]} {c.ordre}. {qui} : {c.question}")
        boutons.append([(f"✅ {c.ordre}", f"c|{c.id}|f"), (f"⏭ {c.ordre}", f"c|{c.id}|p"), (f"🔄 {c.ordre}", f"c|{c.id}|q")])
    texte = (f"🎬 Plan du confessionnal — {_jour_lisible(journee.jour)}\n\n" + "\n\n".join(corps)
             + "\n\nC'est une proposition : à toi de dire ce que vous avez suivi (✅ fait, ⏭ passé, 🔄 autre question).")
    return Message(texte, boutons)


def decider(session: Session, ligne_id: int, action: str, hasard=random) -> Journee | None:
    """Applique un bouton : f (fait, un deuxième appui annule), p (passé), q (changer la question). Renvoie la journée concernée."""
    c = session.get(Confessionnal, ligne_id)
    if c is None:
        return None
    if action in ("f", "p"):
        cible = "fait" if action == "f" else "passe"
        c.statut = "propose" if c.statut == cible else cible
        c.decide_a = maintenant() if c.statut == "fait" else None
    elif action == "q" and c.statut == "propose":
        if c.type == "duo":
            a, b = session.get(Personne, c.personne_id), session.get(Personne, c.autre_id)
            c.question = hasard.choice([d for d in DUOS if d.format(a=a.prenom, b=b.prenom) != c.question] or DUOS).format(a=a.prenom, b=b.prenom)
        else:
            utilisees = {x.question for x in lignes(session, session.get(Journee, c.journee_id))}
            c.type, c.question = "libre", _libre(session, session.get(Personne, c.personne_id), utilisees, hasard)
    session.commit()
    return session.get(Journee, c.journee_id)


def suivi(session: Session) -> str:
    """Combien de confessionnaux chacun a faits ou passés depuis le début."""
    lignes_ = []
    for p in session.scalars(select(Personne).order_by(Personne.id)):
        fait, passe = (session.scalar(select(func.count()).select_from(Confessionnal).where(Confessionnal.personne_id == p.id, Confessionnal.statut == s)) for s in ("fait", "passe"))
        if fait or passe:
            lignes_.append(f"• {p.prenom} : {fait} fait{'s' if fait > 1 else ''}, {passe} passé{'s' if passe > 1 else ''}")
    return "Depuis le début :\n" + "\n".join(lignes_) if lignes_ else "Aucun confessionnal enregistré pour l'instant."


def evenements(session: Session, hasard=random) -> list[Evenement]:
    """Le plan du soir part à l'organisateur, en privé, à l'heure du rappel du soir, une seule fois par journée de ski."""
    local = vers_paris(maintenant())
    journee = session.scalar(select(Journee).where(Journee.jour == local.date(), Journee.ski.is_(True)))
    orga = organisateur(session)
    if journee is None or orga is None or orga.telegram_id is None or local.time() < _heure(config.HEURE_SOIR):
        return []
    if reglage(session, f"confessionnal:{journee.id}") is not None or not _presents(session, journee):
        return []
    definir_reglage(session, f"confessionnal:{journee.id}", "1")
    proposer_plan(session, journee, hasard)
    m = message(session, journee)
    return [Evenement(orga.telegram_id, m.texte, boutons=m.boutons)]
