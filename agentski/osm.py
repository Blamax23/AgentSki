"""Pistes, remontées et restaurants du domaine d'après OpenStreetMap (© contributeurs OpenStreetMap, licence ODbL).

Une seule requête au service public Overpass, gardée un mois dans data/osm_domaine.json : le bot ne rappelle jamais le service
à chaque position reçue. Les distances sont à vol d'oiseau, pas par les pistes ni les remontées."""
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

ZONE = (45.975, 6.625, 46.095, 6.800)  # sud, ouest, nord, est : le même rectangle resserré que pour la liste des pistes
FICHIER = config.ROOT / "data" / "osm_domaine.json"
DUREE_VIE = 30 * 24 * 3600
DONNEES: dict | None = None  # remplacé dans les tests


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _requete() -> dict:
    texte = ("[out:json][timeout:90];(" "way[\"piste:type\"=\"downhill\"][\"name\"](%s,%s,%s,%s);"
             "node[\"aerialway\"=\"station\"][\"name\"](%s,%s,%s,%s);"
             "node[\"amenity\"~\"^(restaurant|bar|cafe|fast_food)$\"][\"name\"](%s,%s,%s,%s););out geom;") % (ZONE * 3)
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=urllib.parse.urlencode({"data": texte}).encode(), headers={
        "User-Agent": "chronique-ski-bot/0.1 (projet personnel, pistes et points de rendez-vous du Grand Massif)", "Accept": "*/*"})
    for essai in range(4):  # le service public renvoie parfois une erreur passagère (502, 504) quand il est chargé
        try:
            brut = json.load(urllib.request.urlopen(req, timeout=120))["elements"]
            break
        except urllib.error.HTTPError as erreur:
            if essai == 3 or erreur.code not in (429, 502, 503, 504):
                raise
            time.sleep(10 * (essai + 1))
    pistes, poi = [], []
    for e in brut:
        t = e.get("tags", {})
        if e["type"] == "way" and e.get("geometry"):
            pistes.append({"nom": " ".join(t["name"].split()), "pts": [[g["lat"], g["lon"]] for g in e["geometry"]]})
        elif e["type"] == "node":
            poi.append({"nom": " ".join(t["name"].split()), "type": "remontée" if t.get("aerialway") else "restaurant", "lat": e["lat"], "lon": e["lon"]})
    return {"pistes": pistes, "poi": poi}


def charger(fichier: Path | None = None, requete=_requete) -> dict:
    """Les données du domaine, lues du cache s'il a moins d'un mois, sinon redemandées à OpenStreetMap."""
    global DONNEES
    if DONNEES is not None:
        return DONNEES
    fichier = fichier or FICHIER
    if fichier.exists() and time.time() - fichier.stat().st_mtime < DUREE_VIE:
        DONNEES = json.loads(fichier.read_text(encoding="utf-8"))
        return DONNEES
    donnees = requete()
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    DONNEES = donnees
    return DONNEES


def _distance_segment(lat, lon, a, b) -> float:
    """Distance d'un point à un segment, dans une projection locale (assez juste à cette échelle)."""
    kx, ky = math.cos(math.radians(lat)) * 111320, 110540
    px, py = (lon - a[1]) * kx, (lat - a[0]) * ky
    bx, by = (b[1] - a[1]) * kx, (b[0] - a[0]) * ky
    longueur2 = bx * bx + by * by
    t = 0.0 if longueur2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / longueur2))
    return math.hypot(px - t * bx, py - t * by)


def piste_proche(lat: float, lon: float, rayon_m: float = 40, donnees: dict | None = None) -> str | None:
    """Le nom de la piste la plus proche du point, si elle est à moins de `rayon_m` mètres (la précision GPS interdit d'aller plus fin)."""
    meilleure, distance = None, rayon_m
    for piste in (donnees or charger())["pistes"]:
        pts = piste["pts"]
        for a, b in zip(pts, pts[1:]):
            d = _distance_segment(lat, lon, a, b)
            if d < distance:
                meilleure, distance = piste["nom"], d
    return meilleure
