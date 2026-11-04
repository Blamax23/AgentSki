"""Météo et neige des cinq stations, via Open-Meteo (gratuit sans clé pour un usage non commercial, licence CC-BY 4.0).

Les altitudes des villages viennent du géocodage d'Open-Meteo. Les prévisions sont des estimations de modèle, calculées au
village et à 2 000 m (altitude fixe, choisie ici) : elles ne remplacent ni le plan des pistes ni le bulletin avalanche."""
import json
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

STATIONS_GEO = {  # latitude, longitude, altitude du village (m)
    "Flaine": (46.00656, 6.68956, 1613),
    "Les Carroz": (46.02665, 6.64038, 1139),
    "Morillon": (46.08341, 6.67625, 688),
    "Samoëns": (46.08282, 6.72647, 706),
    "Sixt": (46.0548, 6.77436, 763),
}
ALTITUDE_HAUTE = 2000
URL = "https://api.open-meteo.com/v1/forecast"
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
CODES = [((0,), "☀️", "ciel dégagé"), ((1, 2), "🌤", "peu nuageux"), ((3,), "☁️", "couvert"), ((45, 48), "🌫", "brouillard"),
         ((51, 53, 55, 56, 57), "🌦", "bruine"), ((61, 63, 65, 66, 67, 80, 81, 82), "🌧", "pluie"),
         ((71, 73, 75, 77, 85, 86), "🌨", "neige"), ((95, 96, 99), "⛈", "orage")]
_cache: dict[tuple, tuple[float, dict]] = {}


def normaliser(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn").lower().strip()


def trouver_station(nom: str) -> str | None:
    cible = normaliser(nom)
    return next((s for s in STATIONS_GEO if normaliser(s) == cible), None)


def trouver_jour(texte: str, aujourdhui: date) -> date | None:
    """« demain », « samedi », « 2026-12-12 » ou « 12/12 » ; vide = aujourd'hui."""
    t = normaliser(texte)
    if t in ("", "aujourd'hui", "aujourdhui", "auj"):
        return aujourdhui
    if t == "demain":
        return aujourdhui + timedelta(days=1)
    if t in ("apres-demain", "apres demain"):
        return aujourdhui + timedelta(days=2)
    if t in JOURS:
        return aujourdhui + timedelta(days=(JOURS.index(t) - aujourdhui.weekday()) % 7)
    for forme in ("%Y-%m-%d", "%d/%m"):
        try:
            d = datetime.strptime(t, forme).date()
            return d if forme == "%Y-%m-%d" else d.replace(year=aujourdhui.year)
        except ValueError:
            continue
    return None


def phrase_code(code: int) -> tuple[str, str]:
    return next(((e, t) for codes, e, t in CODES if code in codes), ("❔", "indéterminé"))


# --- Appel ---------------------------------------------------------------------------------------

def appeler(station: str, altitude: int, jours: int = 4) -> dict:
    """Prévisions Open-Meteo d'une station à une altitude, avec la veille ; gardées dix minutes."""
    cle = (station, altitude, jours)
    if cle in _cache and time.time() - _cache[cle][0] < 600:
        return _cache[cle][1]
    lat, lon, _ = STATIONS_GEO[station]
    requete = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "elevation": altitude, "timezone": "Europe/Paris", "past_days": 1, "forecast_days": jours,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,snowfall_sum,precipitation_sum,wind_gusts_10m_max",
        "hourly": "temperature_2m,snowfall,snow_depth,wind_speed_10m,wind_gusts_10m,freezing_level_height,weather_code"})
    with urllib.request.urlopen(f"{URL}?{requete}", timeout=30) as r:
        donnees = json.load(r)
    _cache[cle] = (time.time(), donnees)
    return donnees


# --- Lecture ---------------------------------------------------------------------------------------

def etat_actuel(donnees: dict, maintenant: datetime) -> dict:
    """Conditions à l'heure `maintenant` (heure de Paris) et neige tombée sur les 24 h précédentes."""
    h = donnees["hourly"]
    i = h["time"].index(maintenant.strftime("%Y-%m-%dT%H:00"))
    return {"temperature": h["temperature_2m"][i], "vent": h["wind_speed_10m"][i], "rafales": h["wind_gusts_10m"][i],
            "neige_sol_cm": round((h["snow_depth"][i] or 0) * 100), "isotherme": h["freezing_level_height"][i],
            "code": h["weather_code"][i], "neige_24h": round(sum(x or 0 for x in h["snowfall"][max(0, i - 23):i + 1]), 1)}


def resume_jour(donnees: dict, jour: date) -> dict | None:
    d = donnees["daily"]
    iso = jour.isoformat()
    if iso not in d["time"]:
        return None
    i = d["time"].index(iso)
    iso_h = [x for x, t in zip(donnees["hourly"]["freezing_level_height"], donnees["hourly"]["time"]) if t.startswith(iso) and x is not None]
    return {"code": d["weather_code"][i], "tmin": d["temperature_2m_min"][i], "tmax": d["temperature_2m_max"][i],
            "neige": d["snowfall_sum"][i], "pluie": d["precipitation_sum"][i], "rafales": d["wind_gusts_10m_max"][i],
            "isotherme": round(sum(iso_h) / len(iso_h)) if iso_h else None}


# --- Textes -------------------------------------------------------------------------------------------------

def texte_info(station: str, maintenant: datetime, appel=None) -> str:
    appel = appel or appeler
    _, _, village = STATIONS_GEO[station]
    bas, haut = etat_actuel(appel(station, village), maintenant), etat_actuel(appel(station, ALTITUDE_HAUTE), maintenant)
    emoji, ciel = phrase_code(haut["code"])
    return (f"🏔 {station} — conditions maintenant\n"
            f"{emoji} {ciel} à {ALTITUDE_HAUTE} m, {haut['temperature']:.0f} °C (village {bas['temperature']:.0f} °C)\n"
            f"💨 Vent {haut['vent']:.0f} km/h, rafales {haut['rafales']:.0f} à {ALTITUDE_HAUTE} m\n"
            f"❄️ Neige au sol : {bas['neige_sol_cm']} cm au village ({village} m), {haut['neige_sol_cm']} cm à {ALTITUDE_HAUTE} m\n"
            f"🌨 Tombée sur 24 h : {bas['neige_24h']} cm au village, {haut['neige_24h']} cm à {ALTITUDE_HAUTE} m\n"
            f"🌡 Isotherme 0 °C vers {haut['isotherme']:.0f} m\n"
            "Estimations d'un modèle météo, pas des relevés de la station.")


def texte_meteo(station: str, jour: date, appel=None) -> str:
    appel = appel or appeler
    _, _, village = STATIONS_GEO[station]
    bas, haut = resume_jour(appel(station, village), jour), resume_jour(appel(station, ALTITUDE_HAUTE), jour)
    if bas is None or haut is None:
        return f"Pas de prévision pour le {jour.day:02d}/{jour.month:02d} (je vois 3 jours devant moi)."
    emoji, ciel = phrase_code(haut["code"])
    return (f"{emoji} {station} — {JOURS[jour.weekday()]} {jour.day:02d}/{jour.month:02d} : {ciel}\n"
            f"🌡 À {ALTITUDE_HAUTE} m : {haut['tmin']:.0f} à {haut['tmax']:.0f} °C · village : {bas['tmin']:.0f} à {bas['tmax']:.0f} °C\n"
            f"🌨 Neige : {haut['neige']:.0f} cm à {ALTITUDE_HAUTE} m, {bas['neige']:.0f} cm au village\n"
            f"💨 Rafales jusqu'à {haut['rafales']:.0f} km/h à {ALTITUDE_HAUTE} m\n"
            f"🌡 Isotherme 0 °C vers {haut['isotherme']} m")


def texte_point_matin(jour: date, appel=None) -> str:
    appel = appel or appeler
    lignes = [f"🌤 Point météo — {JOURS[jour.weekday()]} {jour.day:02d}/{jour.month:02d}"]
    for station, (_, _, village) in STATIONS_GEO.items():
        bas, haut = resume_jour(appel(station, village), jour), resume_jour(appel(station, ALTITUDE_HAUTE), jour)
        emoji, _ = phrase_code(haut["code"])
        lignes.append(f"{emoji} {station} : village {bas['tmin']:.0f}/{bas['tmax']:.0f} °C · {ALTITUDE_HAUTE} m {haut['tmin']:.0f}/{haut['tmax']:.0f} °C · "
                      f"neige {haut['neige']:.0f} cm · rafales {haut['rafales']:.0f} km/h")
    return "\n".join(lignes)


def donnees_briefing(jours: list[date], appel=None) -> str:
    """Faits bruts par station et par jour, donnés au modèle qui rédige le briefing : il n'a droit qu'à eux."""
    appel = appel or appeler
    lignes = []
    for station, (_, _, village) in STATIONS_GEO.items():
        for jour in jours:
            bas, haut = resume_jour(appel(station, village), jour), resume_jour(appel(station, ALTITUDE_HAUTE), jour)
            if bas and haut:
                _, ciel = phrase_code(haut["code"])
                lignes.append(f"{station} (village {village} m) {JOURS[jour.weekday()]} : {ciel}, {haut['tmin']:.0f} à {haut['tmax']:.0f} °C à "
                              f"{ALTITUDE_HAUTE} m, neige {haut['neige']:.0f} cm, rafales {haut['rafales']:.0f} km/h, isotherme 0 °C vers {haut['isotherme']} m")
    return "\n".join(lignes)
