"""Télécharge sur ton disque les médias reçus, rangés par jour et par personne : médias/2027-01-02/Adrien/181530_12.mp4.
L'API des bots Telegram ne remet que les fichiers de 20 Mo ou moins : les plus gros sont listés, à récupérer avec /medias
(le bot les renvoie dans Telegram, d'où ils se téléchargent en taille réelle). Cœur testable : le client Telegram est injecté."""
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .horloge import vers_paris
from .models import Media, Personne

LIMITE = 20 * 1024 * 1024
EXTENSIONS = {"photo": "jpg", "video": "mp4", "animation": "mp4", "voice": "ogg"}


class TropGros(Exception):
    """Telegram refuse de remettre ce fichier à un bot."""


class ClientTelegram:
    """Le client réel. Le jeton ne sort jamais de cette classe : il n'est ni affiché ni écrit dans un journal."""

    def __init__(self, jeton: str) -> None:
        self._jeton = jeton
        self._http = httpx.Client(timeout=120)

    def chemin(self, file_id: str) -> str:
        r = self._http.get(f"https://api.telegram.org/bot{self._jeton}/getFile", params={"file_id": file_id})
        corps = r.json()
        if not corps.get("ok"):
            if "too big" in str(corps.get("description", "")).lower():
                raise TropGros
            raise RuntimeError(corps.get("description", "réponse inattendue de Telegram"))
        return corps["result"]["file_path"]

    def contenu(self, chemin: str) -> bytes:
        r = self._http.get(f"https://api.telegram.org/file/bot{self._jeton}/{chemin}")
        r.raise_for_status()
        return r.content


@dataclass
class Bilan:
    telecharges: int = 0
    deja_la: int = 0
    gros: list[str] = field(default_factory=list)      # description de chaque fichier trop lourd pour l'API
    echecs: list[str] = field(default_factory=list)


def telecharger_tout(session: Session, dossier: Path, client) -> Bilan:
    bilan = Bilan()
    for m in session.scalars(select(Media).order_by(Media.cree_a, Media.id)):
        prenom = session.get(Personne, m.personne_id).prenom
        heure = vers_paris(m.cree_a.replace(tzinfo=None))
        cible = dossier / f"{heure:%Y-%m-%d}" / prenom
        nom = f"{heure:%H%M%S}_{m.id}"
        description = f"{heure:%d/%m %H:%M} · {prenom} · {m.type} ({(m.taille or 0) // (1024 * 1024)} Mo)"
        if any(cible.glob(f"{nom}.*")):
            bilan.deja_la += 1
            continue
        if (m.taille or 0) > LIMITE:
            bilan.gros.append(description)
            continue
        try:
            chemin = client.chemin(m.file_id)
            donnees = client.contenu(chemin)
        except TropGros:
            bilan.gros.append(description)
            continue
        except Exception as e:
            bilan.echecs.append(f"{description} : {e}")
            continue
        extension = EXTENSIONS.get(m.type) or Path(chemin).suffix.lstrip(".") or "bin"
        cible.mkdir(parents=True, exist_ok=True)
        temporaire = cible / f"{nom}.part"
        temporaire.write_bytes(donnees)
        temporaire.rename(cible / f"{nom}.{extension}")  # jamais un fichier à moitié écrit sous son vrai nom
        bilan.telecharges += 1
    return bilan
