"""Source unique de l'heure, remplaçable par une horloge accélérée pour les simulations."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

_fixe: datetime | None = None


def maintenant() -> datetime:
    """Heure actuelle en UTC naïf, le format stocké en base."""
    if _fixe is not None:
        return _fixe
    return datetime.now(timezone.utc).replace(tzinfo=None)


def regler(dt: datetime | None) -> None:
    """Fixe l'horloge (UTC naïf). None revient à l'heure réelle."""
    global _fixe
    _fixe = dt


def vers_utc(local: datetime) -> datetime:
    """Heure de Paris naïve -> UTC naïf (gère l'heure d'été)."""
    return local.replace(tzinfo=PARIS).astimezone(timezone.utc).replace(tzinfo=None)


def vers_paris(utc: datetime) -> datetime:
    return utc.replace(tzinfo=timezone.utc).astimezone(PARIS).replace(tzinfo=None)
