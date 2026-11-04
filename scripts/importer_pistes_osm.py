"""Récupère les noms de pistes de descente du Grand Massif dans OpenStreetMap (© contributeurs OpenStreetMap, licence ODbL)
et les écrit dans donnees/pistes.txt, en gardant les pistes déjà connues en tête de liste.

Lancer : .venv/bin/python scripts/importer_pistes_osm.py          (affiche seulement)
         .venv/bin/python scripts/importer_pistes_osm.py --ecrire  (met à jour donnees/pistes.txt)
La zone est resserrée autour des cinq stations : un rectangle plus large ramasse Chamonix, Praz-de-Lys ou Plaine-Joux."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from agentski.entretien import _norm  # noqa: E402

ZONE = (45.975, 6.625, 46.095, 6.800)  # sud, ouest, nord, est
PREFIXES_EXCLUS = ("acces", "retour", "raccord", "traversee", "route", "chemin", "ancien", "piste ", "front de neige")
FICHIER = RACINE / "donnees" / "pistes.txt"

requete = "[out:json][timeout:60];(way[\"piste:type\"=\"downhill\"][\"name\"](%s,%s,%s,%s););out tags center;" % ZONE
req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=urllib.parse.urlencode({"data": requete}).encode(), headers={
    "User-Agent": "chronique-ski-bot/0.1 (projet personnel, liste des pistes du Grand Massif)", "Accept": "*/*"})
for essai in range(4):  # le service public renvoie parfois une erreur passagère (502, 504) quand il est chargé
    try:
        elements = json.load(urllib.request.urlopen(req, timeout=90))["elements"]
        break
    except urllib.error.HTTPError as erreur:
        if essai == 3 or erreur.code not in (429, 502, 503, 504):
            raise
        print(f"Service occupé ({erreur.code}), nouvel essai dans {10 * (essai + 1)} s…")
        time.sleep(10 * (essai + 1))

retenues: dict[str, tuple[str, int]] = {}
ecartees = []
for e in elements:
    nom = " ".join(e["tags"]["name"].split())
    cle = _norm(nom)
    if e["tags"].get("piste:difficulty") == "freeride" or cle.startswith(PREFIXES_EXCLUS) or "variante" in cle or "stade" in cle:
        ecartees.append(nom)
        continue
    if cle not in retenues or sum(c.isupper() or not c.isascii() for c in nom) > sum(c.isupper() or not c.isascii() for c in retenues[cle][0]):
        retenues[cle] = (nom, retenues.get(cle, ("", 0))[1] + 1)  # on garde l'orthographe la plus soignée (majuscule, accents)
existantes = [l.strip() for l in FICHIER.read_text(encoding="utf-8").splitlines() if l.strip()] if FICHIER.exists() else []
connues = {_norm(n) for n in existantes}
nouvelles = sorted((n for cle, (n, _) in retenues.items() if cle not in connues), key=_norm)
print(f"{len(elements)} tronçons nommés, {len(retenues)} pistes retenues, {len(set(ecartees))} noms écartés (liaisons, accès, variantes, hors-piste)")
print(f"{len(existantes)} déjà connues, {len(nouvelles)} nouvelles")
print(", ".join(nouvelles))
if "--ecrire" in sys.argv:
    FICHIER.write_text("\n".join(existantes + nouvelles) + "\n", encoding="utf-8")
    print(f"Écrit dans {FICHIER.relative_to(RACINE)} : {len(existantes) + len(nouvelles)} pistes.")
