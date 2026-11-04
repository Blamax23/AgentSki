"""Accès au bot : un inconnu reçoit une question ; avec la bonne réponse, il choisit qui il est parmi les personnes
pas encore rattachées à un compte Telegram. Cœur indépendant de Telegram."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Personne


def normaliser(texte: str) -> str:
    """Majuscules, espaces superflus retirés : « le  secret » vaut « LE SECRET »."""
    return " ".join(texte.upper().split())


def acces_actif() -> bool:
    return bool(config.REPONSE_ACCES.strip())


def reponse_correcte(texte: str) -> bool:
    return acces_actif() and normaliser(texte) == normaliser(config.REPONSE_ACCES)


def personne_connue(session: Session, telegram_id: int) -> Personne | None:
    return session.scalar(select(Personne).where(Personne.telegram_id == telegram_id))


def personnes_libres(session: Session) -> list[Personne]:
    """Personnes attendues qui n'ont pas encore de compte Telegram rattaché."""
    return list(session.scalars(select(Personne).where(Personne.telegram_id.is_(None)).order_by(Personne.id)))


def rattacher(session: Session, telegram_id: int, prenom: str) -> Personne | None:
    """Rattache un compte Telegram à une personne libre. None si elle est déjà prise ou si le compte est déjà connu."""
    if personne_connue(session, telegram_id) is not None:
        return None
    personne = session.scalar(select(Personne).where(Personne.prenom == prenom, Personne.telegram_id.is_(None)))
    if personne is None:
        return None
    personne.telegram_id = telegram_id
    session.commit()
    return personne


class Tentatives:
    """Essais ratés par compte Telegram, en mémoire : trop d'échecs bloquent le compte un moment."""

    def __init__(self) -> None:
        self._echecs: dict[int, int] = {}
        self._bloque_jusqua: dict[int, datetime] = {}

    def bloque(self, telegram_id: int, maintenant: datetime) -> bool:
        fin = self._bloque_jusqua.get(telegram_id)
        if fin is not None and maintenant < fin:
            return True
        if fin is not None:
            del self._bloque_jusqua[telegram_id]
            self._echecs.pop(telegram_id, None)
        return False

    def rater(self, telegram_id: int, maintenant: datetime) -> int:
        """Enregistre un échec ; renvoie le nombre d'essais restants (0 = compte bloqué)."""
        self._echecs[telegram_id] = self._echecs.get(telegram_id, 0) + 1
        restant = config.ESSAIS_MAX - self._echecs[telegram_id]
        if restant <= 0:
            self._bloque_jusqua[telegram_id] = maintenant + timedelta(minutes=config.BLOCAGE_MIN)
            return 0
        return restant

    def reussir(self, telegram_id: int) -> None:
        self._echecs.pop(telegram_id, None)
