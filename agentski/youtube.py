"""Statistiques des épisodes publiés sur YouTube : /episode pour en enregistrer un, /vues pour les chiffres.
GET videos?part=statistics (YouTube Data API v3, 1 unité de quota par appel sur 10 000 par jour gratuits)."""
import json
import re
import urllib.parse
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Episode

URL = "https://www.googleapis.com/youtube/v3/videos"
VIDEO = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def extraire_video(texte: str) -> str | None:
    trouve = VIDEO.search(texte)
    if trouve:
        return trouve.group(1)
    return texte.strip() if re.fullmatch(r"[A-Za-z0-9_-]{11}", texte.strip()) else None


def enregistrer_episode(session: Session, args: list[str]) -> str:
    """/episode https://youtu.be/xxxxxxxxxxx Titre de l'épisode"""
    if not args or not extraire_video(args[0]):
        return "Format : /episode https://youtu.be/xxxxxxxxxxx Titre de l'épisode"
    video = extraire_video(args[0])
    dernier = session.scalar(select(Episode.id).order_by(Episode.id.desc()).limit(1)) or 0
    titre = " ".join(args[1:]).strip() or f"Épisode {dernier + 1}"
    if session.scalar(select(Episode).where(Episode.video_id == video)):
        return "Cet épisode est déjà enregistré."
    session.add(Episode(titre=titre, video_id=video))
    session.commit()
    return f"▶️ Épisode enregistré : {titre}. /vues pour ses chiffres."


def _http(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def vues(session: Session, http=_http) -> str:
    episodes = list(session.scalars(select(Episode).order_by(Episode.id)))
    if not episodes:
        return "Aucun épisode enregistré. /episode https://youtu.be/xxxxxxxxxxx Titre"
    if not config.YOUTUBE_API_KEY:
        return "YouTube n'est pas branché : il faut une clé d'API (YouTube Data API v3) dans YOUTUBE_API_KEY."
    try:
        r = http(f"{URL}?" + urllib.parse.urlencode({"part": "statistics", "id": ",".join(e.video_id for e in episodes), "key": config.YOUTUBE_API_KEY}))
    except Exception:
        return "YouTube ne répond pas pour l'instant, réessaie dans un moment."
    stats = {v["id"]: v.get("statistics", {}) for v in r.get("items", [])}
    lignes = ["📺 Chiffres des épisodes"]
    for e in episodes:
        s = stats.get(e.video_id)
        if s is None:
            lignes.append(f"• {e.titre} : introuvable (vidéo supprimée ou privée ?)")
            continue
        morceaux = [f"{int(s['viewCount']):,} vue".replace(",", " ") + ("s" if int(s["viewCount"]) > 1 else "")] if "viewCount" in s else []
        if "likeCount" in s:
            morceaux.append(f"{int(s['likeCount'])} j'aime")
        if "commentCount" in s:
            morceaux.append(f"{int(s['commentCount'])} commentaire{'s' if int(s['commentCount']) > 1 else ''}")
        lignes.append(f"• {e.titre} : " + (" · ".join(morceaux) or "pas de chiffres"))
    return "\n".join(lignes)
