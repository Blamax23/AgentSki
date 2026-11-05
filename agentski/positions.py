"""Position en direct : /ou, /rdv, pistes et secteurs déduits le soir, effacement après le week-end. Cœur indépendant de Telegram.

La position sert à répondre à « où ? », jamais aux vitesses : la précision d'un téléphone dans le froid, avec des trous de réseau,
ne s'y prête pas. Les distances sont à vol d'oiseau. Les positions brutes sont effacées une fois le week-end terminé."""
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import meteo, osm
from .amis import _maintenant_aware, aujourdhui, definir_reglage, journee_du_jour, personne_par_prenom, reglage
from .entretien import _norm
from .horloge import maintenant, vers_paris
from .models import ChampJournal, Journee, Personne, Position, Weekend

FRAICHEUR_MIN = 30        # au-delà, une position n'est plus « où est-il maintenant »
PRECISION_MAX_M = 50      # pour déduire une piste : plus vague, on ne dit rien
PORTEE_STATION_M = 4000
PORTEE_RDV_M = 3000


def _age(recue_a: datetime) -> str:
    minutes = int((maintenant() - recue_a).total_seconds() // 60)
    return "à l'instant" if minutes < 1 else (f"il y a {minutes} min" if minutes < 90 else f"il y a {minutes // 60} h")


def enregistrer_position(session: Session, personne: Personne, lat: float, lon: float, precision: float | None) -> bool:
    """Garde la position, sauf si elle ne change rien (moins de 20 m en moins de 2 minutes). Partager vaut présence."""
    derniere = session.scalar(select(Position).where(Position.personne_id == personne.id).order_by(Position.id.desc()).limit(1))
    if derniere and osm.distance_m(derniere.lat, derniere.lon, lat, lon) < 20 and (maintenant() - derniere.recue_a) < timedelta(minutes=2):
        return False
    journee = journee_du_jour(session, personne)
    session.add(Position(personne_id=personne.id, journee_id=journee.id, lat=lat, lon=lon, precision_m=precision, recue_a=maintenant()))
    session.commit()
    return True


def _derniere(session: Session, personne_id: int) -> Position | None:
    return session.scalar(select(Position).where(Position.personne_id == personne_id).order_by(Position.id.desc()).limit(1))


def _fraiches(session: Session) -> list[tuple[Personne, Position]]:
    limite = maintenant() - timedelta(minutes=FRAICHEUR_MIN)
    paires = [(p, _derniere(session, p.id)) for p in session.scalars(select(Personne).order_by(Personne.id))]
    return [(p, pos) for p, pos in paires if pos is not None and pos.recue_a >= limite]


def _village(lat: float, lon: float) -> tuple[str, float] | None:
    nom, d = min(((n, osm.distance_m(lat, lon, la, lo)) for n, (la, lo, _) in meteo.STATIONS_GEO.items()), key=lambda x: x[1])
    return (nom, d) if d <= PORTEE_STATION_M else None


def _description(pos: Position, donnees: dict | None) -> str:
    morceaux = []
    secteur = _village(pos.lat, pos.lon)
    morceaux.append(f"secteur {secteur[0]} (à {secteur[1] / 1000:.1f} km du village)" if secteur else "hors des cinq stations")
    try:
        piste = osm.piste_proche(pos.lat, pos.lon, donnees=donnees) if (pos.precision_m or 0) <= PRECISION_MAX_M else None
    except Exception:
        piste = None  # la carte est indisponible : on répond quand même avec le secteur
    if piste:
        morceaux.append(f"sur ou près de la piste {piste}")
    return ", ".join(morceaux)


# --- /ou ----------------------------------------------------------------------------------------------------

def ou(session: Session, prenom: str | None, donnees: dict | None = None) -> tuple[str, list[tuple[str, float, float]]]:
    """Où est quelqu'un (ou tout le monde). Renvoie le texte et les positions à envoyer comme épingles."""
    if prenom:
        personne = personne_par_prenom(session, prenom)
        if personne is None:
            return f"Je ne connais pas « {prenom} ».", []
        pos = _derniere(session, personne.id)
        if pos is None:
            return f"Pas de position pour {personne.prenom} : sa position en direct doit être partagée dans Telegram (/position explique comment).", []
        cibles = [(personne, pos)]
    else:
        cibles = _fraiches(session)
        if not cibles:
            return "Personne ne partage sa position en direct pour l'instant. /position explique comment faire.", []
    lignes = []
    for personne, pos in cibles:
        ancienne = " (position ancienne)" if (maintenant() - pos.recue_a) > timedelta(minutes=FRAICHEUR_MIN) else ""
        lignes.append(f"📍 {personne.prenom} : {_description(pos, donnees)}, {_age(pos.recue_a)}{ancienne}"
                      + (f" (précision {pos.precision_m:.0f} m)" if pos.precision_m else ""))
    return "\n".join(lignes), [(p.prenom, pos.lat, pos.lon) for p, pos in cibles][:5]


# --- /rdv -----------------------------------------------------------------------------------------------------

def _compter_rdv(session: Session, personne: Personne) -> None:
    cle = f"rdv_count:{personne.id}"
    definir_reglage(session, cle, str(int(reglage(session, cle) or 0) + 1))


def rdv(session: Session, demandeur: Personne, donnees: dict | None = None) -> tuple[str, tuple[float, float] | None]:
    """Le lieu nommé (restaurant, gare de remontée) qui minimise la distance du plus éloigné, à vol d'oiseau."""
    fraiches = _fraiches(session)
    if len(fraiches) < 2:
        return "Il faut au moins deux personnes qui partagent leur position en direct pour proposer un point de rendez-vous.", None
    lat = sum(pos.lat for _, pos in fraiches) / len(fraiches)
    lon = sum(pos.lon for _, pos in fraiches) / len(fraiches)
    try:
        candidats = (donnees or osm.charger())["poi"]
    except Exception:
        candidats = []
    meilleur, score = None, None
    for c in candidats:
        plus_loin = max(osm.distance_m(pos.lat, pos.lon, c["lat"], c["lon"]) for _, pos in fraiches)
        if score is None or plus_loin < score:
            meilleur, score = c, plus_loin
    _compter_rdv(session, demandeur)
    if meilleur is None or score > PORTEE_RDV_M:
        return ("📌 Aucun restaurant ni gare de remontée connu à moins de 3 km de tout le monde : voici le milieu du groupe. "
                "Les distances sont à vol d'oiseau."), (lat, lon)
    distances = ", ".join(f"{p.prenom} {osm.distance_m(pos.lat, pos.lon, meilleur['lat'], meilleur['lon']):.0f} m" for p, pos in fraiches)
    return (f"📌 Point de rendez-vous : {meilleur['nom']} ({meilleur['type']}). Distances à vol d'oiseau : {distances}. "
            "Ni les pistes ni les remontées ne sont prises en compte : à toi de juger si c'est faisable."), (meilleur["lat"], meilleur["lon"])


# --- Déduction du soir et effacement -----------------------------------------------------------------------------

def deduire_journee(session: Session, journee: Journee, donnees: dict | None = None) -> list[str]:
    """Pistes et stations parcourues d'après les positions de la journée, ajoutées au journal sans doublon."""
    positions = [p for p in session.scalars(select(Position).where(Position.journee_id == journee.id)) if (p.precision_m or 0) <= PRECISION_MAX_M]
    if not positions:
        return []
    donnees = donnees or osm.charger()
    deja = {_norm(v) for v in session.scalars(select(ChampJournal.valeur).where(
        ChampJournal.journee_id == journee.id, ChampJournal.cle.in_(["piste", "station"]), ChampJournal.supprime.is_(False)))}
    pistes, villages = [], {}
    for pos in positions:
        piste = osm.piste_proche(pos.lat, pos.lon, donnees=donnees)
        if piste and piste not in pistes:
            pistes.append(piste)
        v = _village(pos.lat, pos.lon)
        if v:
            villages[v[0]] = villages.get(v[0], 0) + 1
    ajoutes = []
    for piste in pistes:
        if _norm(piste) not in deja:
            session.add(ChampJournal(journee_id=journee.id, cle="piste", valeur=piste, phrase_source="position (OpenStreetMap)", verifie=False))
            ajoutes.append(piste)
    for nom, n in villages.items():
        if n >= 3 and _norm(nom) not in deja:
            session.add(ChampJournal(journee_id=journee.id, cle="station", valeur=nom, phrase_source="position (OpenStreetMap)", verifie=False))
            ajoutes.append(nom)
    session.commit()
    return ajoutes


def maintenance(session: Session, donnees: dict | None = None) -> None:
    """Chaque minute : déduit les pistes des journées finies (ou du jour, après 21 h), puis efface les positions du week-end terminé."""
    local = vers_paris(maintenant())
    journees = list(session.scalars(select(Journee).join(Position, Position.journee_id == Journee.id).group_by(Journee.id)))
    for j in journees:
        weekend = session.get(Weekend, j.weekend_id)
        fini = weekend.date_fin < local.date()
        if (fini or (j.jour == local.date() and local.hour >= 21)) and reglage(session, f"deduit:{j.id}") is None:
            try:
                deduire_journee(session, j, donnees)
                definir_reglage(session, f"deduit:{j.id}", "1")
            except Exception:
                pass  # carte indisponible : on réessaie, mais l'effacement ci-dessous a lieu quand même
        if fini:
            session.execute(delete(Position).where(Position.journee_id == j.id))
    session.commit()
    session.execute(delete(Position).where(Position.recue_a < maintenant() - timedelta(days=3)))  # filet de sécurité : jamais plus de trois jours
    session.commit()
