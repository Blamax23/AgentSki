"""Les médias reçus (photos, vidéos, vocaux) : rangement des vocaux hors entretien, et liste à renvoyer avec /medias.
Seule la référence Telegram (`file_id`) est gardée, jamais le fichier. Cœur indépendant de Telegram."""
import random
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .amis import _maintenant_aware, aujourdhui
from .horloge import vers_paris
from .models import Journee, Media, Personne
from .rappels import _jour_lisible

PAR_PAGE = 30
ACCUSES = [
    "🎙 Gardé ! Merci {p}, c'est exactement ce qu'il nous faut pour la série.",
    "🎙 Reçu, rangé, précieux. Continue, {p}, plus tu parles, plus on aura de matière.",
    "🎙 Ton vocal est au chaud, {p}. Tu en as un autre en réserve ?",
    "🎙 Parfait {p}. Dans un an, tu seras content de t'entendre.",
]


def nombre_vocaux(session: Session, personne: Personne | None = None) -> int:
    requete = select(func.count()).select_from(Media).where(Media.type == "voice")
    if personne is not None:
        requete = requete.where(Media.personne_id == personne.id)
    return session.scalar(requete)


def ranger_vocal(session: Session, auteur: Personne, file_id: str, taille: int | None, hasard=random) -> str:
    """Garde la référence d'un vocal envoyé hors entretien (rangé dans la journée du jour s'il y en a une)."""
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui()))
    session.add(Media(personne_id=auteur.id, journee_id=journee.id if journee else None, type="voice", file_id=file_id,
                      taille=taille, cree_a=_maintenant_aware()))
    session.commit()
    n = nombre_vocaux(session, auteur)
    return hasard.choice(ACCUSES).format(p=auteur.prenom) + f"\n({n} {'vocaux' if n > 1 else 'vocal'} de toi gardé{'s' if n > 1 else ''}.)"


def _jour_de(m: Media) -> date:
    return vers_paris(m.cree_a.replace(tzinfo=None)).date()


def a_envoyer(session: Session, jour: date | None = None, tout: bool = False, page: int = 1) -> tuple[list[tuple[str, str, str]], int, date | None]:
    """Les médias à renvoyer : (type, file_id, légende), le nombre total, et le jour choisi. Sans jour ni « tout », le dernier jour qui en a."""
    medias = list(session.scalars(select(Media).order_by(Media.cree_a, Media.id)))
    if not tout:
        if jour is None and medias:
            jour = _jour_de(medias[-1])
        medias = [m for m in medias if _jour_de(m) == jour]
    total = len(medias)
    tranche = medias[(page - 1) * PAR_PAGE: page * PAR_PAGE]
    sortie = []
    for m in tranche:
        prenom = session.get(Personne, m.personne_id).prenom
        heure = vers_paris(m.cree_a.replace(tzinfo=None))
        legende = f"{prenom} · {_jour_lisible(heure.date())} {heure:%H:%M}" + (f"\n{m.legende}" if m.legende else "")
        sortie.append((m.type, m.file_id, legende))
    return sortie, total, jour
