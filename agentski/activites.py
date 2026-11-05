"""Fichiers d'activité FIT (Garmin) et GPX : découpage des descentes, dénivelé négatif, vitesse maximale.

Ouvert à tous, en bonus, jamais pour comparer : ni classement ni écart entre personnes. Seuls des résumés sont gardés, jamais la
trace complète (la position brute ne sert qu'à l'instant de l'analyse). L'API personnelle de Garmin n'est pas branchée : sa
référence officielle n'a pas pu être vérifiée ; on envoie à la place le fichier exporté de Garmin Connect."""
import io
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .amis import journee_du_jour
from .horloge import vers_paris
from .models import Activite, Personne
from .rappels import milliers

SEUIL_DESCENTE_M = 50     # une descente compte à partir de 50 m de dénivelé négatif
RETOURNEMENT_M = 15       # un changement de sens n'est pris en compte qu'au-delà de 15 m (écarte le bruit et les creux de télésiège)
DUREE_MIN_S = 20
VITESSE_MAX_CREDIBLE = 40.0  # m/s (144 km/h) : au-delà, c'est un saut de GPS


@dataclass
class Point:
    t: datetime  # UTC naïf
    lat: float
    lon: float
    alt: float
    v: float | None = None  # m/s


@dataclass
class DescenteTrouvee:
    debut: datetime
    fin: datetime
    chute_m: float
    vmax_ms: float
    points: list[tuple[float, float]]  # début, milieu, fin


@dataclass
class Resume:
    debut: datetime
    duree_s: int
    descentes: list[DescenteTrouvee]

    @property
    def denivele_neg(self) -> int:
        return round(sum(d.chute_m for d in self.descentes))

    @property
    def vitesse_max_kmh(self) -> float | None:
        return round(max((d.vmax_ms for d in self.descentes), default=0) * 3.6, 1) or None


# --- Lecture ----------------------------------------------------------------------------------------------------

def lire_gpx(octets: bytes) -> list[Point]:
    points = []
    for e in ET.fromstring(octets).iter():
        if not e.tag.endswith("trkpt"):
            continue
        ele = next((c.text for c in e if c.tag.endswith("ele")), None)
        t = next((c.text for c in e if c.tag.endswith("time")), None)
        if ele is None or t is None:
            continue
        horodatage = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
        horodatage = horodatage.astimezone(timezone.utc) if horodatage.tzinfo else horodatage
        points.append(Point(horodatage.replace(tzinfo=None), float(e.attrib["lat"]), float(e.attrib["lon"]), float(ele)))
    return points


def lire_fit(octets: bytes) -> list[Point]:
    import fitparse
    points = []
    for m in fitparse.FitFile(io.BytesIO(octets)).get_messages("record"):
        d = {c.name: c.value for c in m if c.value is not None}
        alt = d.get("enhanced_altitude", d.get("altitude"))
        if alt is None or "timestamp" not in d or "position_lat" not in d or "position_long" not in d:
            continue
        points.append(Point(d["timestamp"].replace(tzinfo=None), d["position_lat"] * 180 / 2 ** 31, d["position_long"] * 180 / 2 ** 31,
                            float(alt), d.get("enhanced_speed", d.get("speed"))))
    return points


# --- Analyse ------------------------------------------------------------------------------------------------------

def _distance(a: Point, b: Point) -> float:
    r = 6371000
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b.lon - a.lon) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _vitesses(points: list[Point]) -> list[float]:
    """Vitesse en m/s à chaque point : celle de l'appareil si elle existe, sinon calculée ; lissée sur trois points."""
    brutes = [p.v if p.v is not None else 0.0 for p in points]
    if any(p.v is None for p in points):
        brutes = [0.0] + [_distance(a, b) / max((b.t - a.t).total_seconds(), 1) for a, b in zip(points, points[1:])]
    brutes = [v if v <= VITESSE_MAX_CREDIBLE else 0.0 for v in brutes]
    return [sum(brutes[max(0, i - 1):i + 2]) / len(brutes[max(0, i - 1):i + 2]) for i in range(len(brutes))]


def analyser(points: list[Point]) -> Resume | None:
    """Découpe l'activité en descentes (allers-retours d'altitude avec seuil), en ignorant remontées et creux de télésiège."""
    if len(points) < 10:
        return None
    points = sorted(points, key=lambda p: p.t)
    fenetre = [points[max(0, i - 2):i + 3] for i in range(len(points))]
    alt = [sum(p.alt for p in f) / len(f) for f in fenetre]  # altitude lissée
    vit = _vitesses(points)
    descentes: list[DescenteTrouvee] = []

    def clore(s: int, c: int) -> None:
        # Le lissage sert à repérer les descentes ; la chute se mesure sur l'altitude réelle autour du sommet et du creux,
        # sinon le lissage rabote les deux extrémités (4 % de dénivelé perdu sur une descente de 400 m)
        haut = max(p.alt for p in points[max(0, s - 2):s + 3])
        bas = min(p.alt for p in points[max(0, c - 2):c + 3])
        chute = haut - bas
        duree = (points[c].t - points[s].t).total_seconds()
        if chute >= SEUIL_DESCENTE_M and duree >= DUREE_MIN_S:
            m = (s + c) // 2
            descentes.append(DescenteTrouvee(points[s].t, points[c].t, chute, max(vit[s:c + 1]),
                                             [(points[i].lat, points[i].lon) for i in (s, m, c)]))

    etat, sommet, creux = "monte", 0, 0
    for i in range(1, len(points)):
        if etat == "monte":
            if alt[i] > alt[sommet]:
                sommet = i
            if alt[sommet] - alt[i] >= RETOURNEMENT_M:
                etat, creux = "descend", i
        else:
            if alt[i] < alt[creux]:
                creux = i
            if alt[i] - alt[creux] >= RETOURNEMENT_M:
                clore(sommet, creux)
                etat, sommet = "monte", i
    if etat == "descend":
        clore(sommet, creux)
    return Resume(points[0].t, int((points[-1].t - points[0].t).total_seconds()), descentes)


# --- Enregistrement --------------------------------------------------------------------------------------------------

def enregistrer(session: Session, personne: Personne, nom_fichier: str, octets: bytes) -> str:
    extension = nom_fichier.rsplit(".", 1)[-1].lower()
    try:
        points = lire_fit(octets) if extension == "fit" else lire_gpx(octets)
    except Exception:
        return "Je n'arrive pas à lire ce fichier. Envoie le fichier .fit ou .gpx exporté de Garmin Connect, sans le renommer ni le compresser."
    resume = analyser(points)
    if resume is None or not resume.descentes:
        return "Je ne repère aucune descente dans ce fichier (trace trop courte, ou pas d'altitude enregistrée). Rien n'est gardé."
    empreinte = f"{resume.debut:%Y%m%dT%H%M%S}-{resume.duree_s}-{len(resume.descentes)}"
    if session.scalar(select(Activite.id).where(Activite.personne_id == personne.id, Activite.empreinte == empreinte)):
        return "Ce fichier a déjà été envoyé : rien n'est compté deux fois."
    journee = journee_du_jour(session, personne, vers_paris(resume.debut).date())
    session.add(Activite(personne_id=personne.id, journee_id=journee.id, source=extension, empreinte=empreinte, debut=resume.debut,
                         duree_s=resume.duree_s, nb_descentes=len(resume.descentes), denivele_neg=resume.denivele_neg,
                         vitesse_max=resume.vitesse_max_kmh, premiere_descente=resume.descentes[0].debut,
                         derniere_descente=resume.descentes[-1].fin,
                         segments=json.dumps([{"debut": d.debut.isoformat(), "points": d.points, "chute": round(d.chute_m)} for d in resume.descentes])))
    session.commit()
    premiere, derniere = vers_paris(resume.descentes[0].debut), vers_paris(resume.descentes[-1].fin)
    return (f"📈 Activité du {vers_paris(resume.debut):%d/%m} rangée dans ta journée : {len(resume.descentes)} descente{'s' if len(resume.descentes) > 1 else ''}, "
            f"{milliers(resume.denivele_neg)} m de dénivelé négatif"
            + (f", vitesse max {resume.vitesse_max_kmh:.0f} km/h" if resume.vitesse_max_kmh else "")
            + f", de {premiere:%H:%M} à {derniere:%H:%M}. C'est un bonus, jamais utilisé pour te comparer aux autres.")


def totaux(session: Session, personne: Personne, journee_ids: list[int]) -> list[str]:
    """Lignes pour /stats : les totaux des fichiers de la personne, rien si elle n'en a envoyé aucun."""
    lignes = session.execute(select(func.count(), func.sum(Activite.nb_descentes), func.sum(Activite.denivele_neg), func.max(Activite.vitesse_max)).where(
        Activite.personne_id == personne.id, Activite.journee_id.in_(journee_ids))).one()
    if not lignes[0]:
        return []
    texte = f"• fichiers d'activité ({lignes[0]}) : {lignes[1]} descentes mesurées, {milliers(lignes[2])} m de dénivelé négatif"
    return [texte + (f", vitesse max {lignes[3]:.0f} km/h" if lignes[3] else "") + " (pour toi seul : jamais comparé)"]
