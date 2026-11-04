"""Agenda : lecture du lien iCal privé de Maxime (pré-coche les jours de la question du jeudi), export .ics des sorties,
et question à Alix en privé la semaine précédente. Cœur indépendant de Telegram.

Limites connues : les événements récurrents (RRULE) ne sont pas développés ; le flux iCal « auquel le groupe s'abonne »
demande une adresse web publique, donc un port ouvert sur le serveur, ce que la décision du projet exclut : on envoie un fichier .ics."""
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

from icalendar import Calendar, Event
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .amis import _maintenant_aware, aujourdhui, reglage
from .horloge import maintenant, vers_paris
from .models import Journee, Personne, Presence, Weekend
from .rappels import Message, NOMS_JOURS
from .surprises import Evenement, _une_fois

_cache: dict[str, tuple[float, str]] = {}
SKI = re.compile(r"\bski\b", re.I)
OPTIONS_ALIX = ["Samedi", "Dimanche", "Les deux", "Pas de ski", "Je ne sais pas"]


def lire_ical(url: str | None = None) -> str | None:
    """Texte de l'agenda, gardé dix minutes. None si aucun lien n'est configuré ou si la lecture échoue."""
    url = url or config.AGENDA_ICAL_URL
    if not url:
        return None
    if url in _cache and time.time() - _cache[url][0] < 600:
        return _cache[url][1]
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            texte = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    _cache[url] = (time.time(), texte)
    return texte


def _jours_evenement(evenement) -> set[date]:
    debut = evenement.decoded("DTSTART")
    fin = evenement.decoded("DTEND") if evenement.get("DTEND") else None
    if isinstance(debut, datetime):
        debut, fin = debut.date(), (fin.date() if isinstance(fin, datetime) else fin)
        fin = fin or debut
        return {debut + timedelta(days=i) for i in range((fin - debut).days + 1)}
    fin = fin or debut + timedelta(days=1)  # événement sur toute la journée : la fin est exclue
    return {debut + timedelta(days=i) for i in range(max((fin - debut).days, 1))}


def jours_ski(jeudi: date, ical: str | None = None) -> list[date]:
    """Jours du vendredi au dimanche suivants couverts par un événement « Ski » de l'agenda."""
    texte = ical if ical is not None else lire_ical()
    if not texte:
        return []
    cibles = {jeudi + timedelta(days=i) for i in (1, 2, 3)}
    try:
        calendrier = Calendar.from_ical(texte)
    except Exception:
        return []
    trouves: set[date] = set()
    for e in calendrier.walk("VEVENT"):
        if SKI.search(str(e.get("SUMMARY", ""))):
            try:
                trouves |= _jours_evenement(e) & cibles
            except Exception:
                continue
    return sorted(trouves)


def masque_jours(jeudi: date, jours: list[date]) -> int:
    return sum(1 << i for i, dec in enumerate((1, 2, 3)) if jeudi + timedelta(days=dec) in jours)


def exporter_ics(session: Session) -> bytes | None:
    """Fichier .ics des journées de ski déclarées, à importer dans un agenda (pas un abonnement)."""
    journees = list(session.scalars(select(Journee).where(Journee.ski.is_(True)).order_by(Journee.jour)))
    if not journees:
        return None
    cal = Calendar()
    cal.add("prodid", "-//Chronique ski//FR")
    cal.add("version", "2.0")
    for j in journees:
        presents = ", ".join(p for p in session.scalars(select(Personne.prenom).join(Presence, Presence.personne_id == Personne.id).where(
            Presence.journee_id == j.id).order_by(Personne.id)))
        e = Event()
        e.add("summary", "Ski — Grand Massif")
        e.add("dtstart", j.jour)
        e.add("dtend", j.jour + timedelta(days=1))
        e.add("uid", f"ski-{j.jour.isoformat()}@chronique-ski")
        e.add("dtstamp", datetime(2026, 1, 1))
        if presents:
            e.add("description", f"Présents : {presents}")
        cal.add_component(e)
    return cal.to_ical()


# --- Question à Alix ---------------------------------------------------------------------------------------

def vendredi_suivant(jour: date) -> date:
    return jour + timedelta(days=(4 - jour.weekday()) % 7 or 7)


def reponse_alix(session: Session, vendredi: date) -> str | None:
    return reglage(session, f"alix:{vendredi.isoformat()}")


def alix_skie(reponse: str | None) -> bool:
    return reponse in ("Samedi", "Dimanche", "Les deux")


def question_alix(session: Session) -> list[Evenement]:
    """Le lundi à 18 h, une question privée à Alix sur le week-end qui vient ; une seule fois par semaine."""
    local = vers_paris(maintenant())
    if local.weekday() != 0 or local.hour < 18:
        return []
    alix = session.scalar(select(Personne).where(Personne.prenom == "Alix"))
    if alix is None or not alix.telegram_id:
        return []
    vendredi = vendredi_suivant(local.date())
    if not _une_fois(session, f"alix_demande:{vendredi.isoformat()}"):
        return []
    boutons = [[(o, f"al|{vendredi.isoformat()}|{i}") for i, o in enumerate(OPTIONS_ALIX[:3])],
               [(o, f"al|{vendredi.isoformat()}|{i + 3}") for i, o in enumerate(OPTIONS_ALIX[3:])]]
    return [Evenement(alix.telegram_id, "Salut Alix ! Tu skies le week-end prochain ? Je demande pour que Maxime puisse s'organiser.", boutons=boutons)]
