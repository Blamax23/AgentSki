"""Le Jam Spotify du groupe : Spotify n'offre aucune API pour créer ou rejoindre un Jam (rien dans la Web API, seulement l'appli).
L'hôte le lance dans l'appli, copie le lien d'invitation et le donne à Krok avec /jam lien ; Krok le garde et le transmet à tout le monde.
Cœur indépendant de Telegram."""
import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from .amis import _maintenant_aware, definir_reglage, nom_krok, reglage
from .models import Personne, Reglage

LIEN = re.compile(r"https://(?:open\.spotify\.com|spotify\.link|spotify\.app\.link)/\S+", re.I)
PERIME_APRES = timedelta(hours=12)  # un Jam finit quand l'hôte le ferme : au-delà, on prévient que le lien est peut-être mort
CLE = "jam"


def extraire_lien(texte: str) -> str | None:
    trouve = LIEN.search(texte or "")
    return trouve.group(0).rstrip(".,)") if trouve else None


def definir(session: Session, hote: Personne, lien: str) -> None:
    definir_reglage(session, CLE, json.dumps({"lien": lien, "hote_id": hote.id, "depuis": _maintenant_aware().isoformat()}))


def terminer(session: Session) -> bool:
    ligne = session.get(Reglage, CLE)
    if ligne is None:
        return False
    session.delete(ligne)
    session.commit()
    return True


def texte_jam(session: Session, annonce: bool = False) -> str:
    """Le message qui présente le Jam en cours (ou explique comment en lancer un)."""
    brut = reglage(session, CLE)
    if not brut:
        return "Aucun Jam en cours. Pour en lancer un : dans l'appli Spotify, lance un Jam (bouton Jam des appareils), copie le lien d'invitation, puis tape /jam suivi du lien."
    jam = json.loads(brut)
    hote = session.get(Personne, jam["hote_id"])
    from datetime import datetime
    age = _maintenant_aware() - datetime.fromisoformat(jam["depuis"])
    perime = "\n⚠️ Ce lien date de plus de 12 h : le Jam est peut-être terminé." if age > PERIME_APRES else ""
    titre = f"🎧 {nom_krok(hote)} lance un Jam Spotify !" if annonce else f"🎧 Jam en cours, lancé par {nom_krok(hote)} :"
    return f"{titre}\n{jam['lien']}\nOuvre le lien dans l'appli Spotify pour le rejoindre (Premium nécessaire quand on n'est pas à côté de l'hôte).{perime}"


def hote_id(session: Session) -> int | None:
    brut = reglage(session, CLE)
    return json.loads(brut)["hote_id"] if brut else None
