"""Appels à OpenRouter : transcription (Voxtral) et extraction structurée (Haiku). Toujours en ZDR."""
import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from . import config

URL = "https://openrouter.ai/api/v1"
PERSONNES = [prenom for prenom, _ in config.PERSONNES]


def _appeler(chemin: str, corps: dict, delai: int = 90) -> dict:
    req = urllib.request.Request(f"{URL}/{chemin}", data=json.dumps(corps).encode(), headers={
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter {e.code} : {e.read().decode()[:300]}") from None


def transcrire(morceaux: list[Path]) -> tuple[str, float]:
    """Transcrit chaque morceau et les recolle. Renvoie (texte, coût en dollars)."""
    textes, cout = [], 0.0
    for morceau in morceaux:
        r = _appeler("audio/transcriptions", {
            "model": config.MODELE_TRANSCRIPTION, "language": "fr", "provider": {"zdr": True},
            "input_audio": {"data": base64.b64encode(morceau.read_bytes()).decode(), "format": "mp3"}})
        textes.append(r.get("text", "").strip())
        cout += (r.get("usage") or {}).get("cost") or 0
    return " ".join(t for t in textes if t), cout


def _champ(type_):
    return {"type": ["object", "null"], "properties": {"valeur": {"type": type_}, "source": {"type": "string"}},
            "required": ["valeur", "source"], "additionalProperties": False}


def _liste():
    return {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"valeur": {"type": "string"}, "source": {"type": "string"}}, "required": ["valeur", "source"]}}


def _schema() -> dict:
    personne = {"type": "string", "enum": PERSONNES}
    return {"type": "object", "additionalProperties": False,
            "required": ["stations", "pistes", "meteo", "neige", "ton", "moments", "chutes"],
            "properties": {
                "stations": _liste(),
                "pistes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                           "required": ["valeur", "source", "filmeur"],
                           "properties": {"valeur": {"type": "string"}, "source": {"type": "string"},
                                          "filmeur": {"anyOf": [{"type": "string", "enum": PERSONNES}, {"type": "null"}]}}}},
                "meteo": _champ("string"), "neige": _champ("string"), "ton": _champ("string"),
                "moments": _liste(),
                "chutes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                           "required": ["personne", "nombre", "source"],
                           "properties": {"personne": personne, "nombre": {"type": "integer"}, "source": {"type": "string"}}}}}}


CONSIGNE = """Tu extrais le journal de bord d'une journée de ski à partir de la transcription vocale de Maxime, le narrateur (« je » = Maxime ; « j'ai filmé » = Maxime filmait). La transcription peut contenir des erreurs.
Personnes connues : {personnes}. Stations : {stations}. Pistes connues : {pistes}.
Règles :
- N'invente rien. Une information non dite vaut null (champ unique) ou liste vide. Jamais de valeur par défaut.
- Pour chaque valeur renseignée, donne en `source` la phrase exacte de la transcription d'où elle vient.
- Corrige un nom mal transcrit seulement s'il correspond clairement à une personne ou à une piste connue ; sinon garde le mot tel quel.
- `pistes` : les pistes descendues, avec qui filmait si c'est dit.
- `moments` : les moments marquants racontés (une phrase courte chacun), pas les simples détails.
- `chutes` : seulement les chutes dites explicitement, avec le nombre.
- Quelqu'un seulement évoqué (« X aurait détesté ») n'est pas un participant : ne rien en déduire.
Journée : {jour}. Moment raconté dans ce message : {periode}."""


def extraire(transcription: str, jour: str, pistes: list[str], periode: str = "bilan") -> dict:
    """Renvoie l'extraction structurée d'un message de l'entretien (matin, midi, aprem, soir ou bilan)."""
    consigne = CONSIGNE.format(personnes=", ".join(PERSONNES), stations=", ".join(config.STATIONS),
                               pistes=", ".join(pistes), jour=jour, periode=periode)
    r = _appeler("chat/completions", {
        "model": config.MODELE_EXTRACTION, "temperature": 0, "provider": {"zdr": True},
        "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": transcription}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "journal", "strict": True, "schema": _schema()}}},
        delai=120)
    return json.loads(r["choices"][0]["message"]["content"])


def creatif(systeme: str, utilisateur: str, max_tokens: int = 900) -> str:
    """Texte court et libre du modèle créatif (répliques, flash infos, briefing). Toujours en ZDR, sans recherche web.
    Ce modèle réfléchit obligatoirement : l'effort est réduit, sinon la réflexion épuise les jetons et coupe la réponse."""
    r = _appeler("chat/completions", {
        "model": config.MODELE_CREATIF, "temperature": 1.0, "max_tokens": max_tokens, "provider": {"zdr": True},
        "reasoning": {"effort": "low"},
        "messages": [{"role": "system", "content": systeme}, {"role": "user", "content": utilisateur}]}, delai=60)
    choix = r["choices"][0]
    texte = (choix["message"].get("content") or "").strip()
    if not texte or choix.get("finish_reason") == "length":
        raise RuntimeError("réponse vide ou coupée")  # l'appelant retombe sur sa ligne de secours
    return texte


def economique(systeme: str, utilisateur: str, max_tokens: int = 2000) -> str:
    """Texte du modèle économique (listes de courses, vérification d'un gage). Réflexion réduite : sans cela la réponse est vide."""
    r = _appeler("chat/completions", {
        "model": config.MODELE_EXTRACTION, "temperature": 0.3, "max_tokens": max_tokens, "provider": {"zdr": True},
        "reasoning": {"effort": "low"},
        "messages": [{"role": "system", "content": systeme}, {"role": "user", "content": utilisateur}]}, delai=60)
    choix = r["choices"][0]
    texte = (choix["message"].get("content") or "").strip()
    if not texte or choix.get("finish_reason") == "length":
        raise RuntimeError("réponse vide ou coupée")
    return texte


def recherche_web(demande: str, max_resultats: int = 3) -> tuple[str, list[str]]:
    """Réponse du modèle créatif appuyée sur une recherche web, avec les adresses citées.
    Le ZDR ne couvre pas la recherche : `demande` ne doit contenir aucune donnée personnelle."""
    r = _appeler("chat/completions", {
        "model": config.MODELE_CREATIF, "max_tokens": 900, "provider": {"zdr": True}, "reasoning": {"effort": "low"},
        "plugins": [{"id": "web", "max_results": max_resultats}],
        "messages": [{"role": "user", "content": demande}]}, delai=120)
    choix = r["choices"][0]
    texte = (choix["message"].get("content") or "").strip()
    if not texte or choix.get("finish_reason") == "length":
        raise RuntimeError("réponse vide ou coupée")
    urls = list(dict.fromkeys(a["url_citation"]["url"] for a in (choix["message"].get("annotations") or []) if "url_citation" in a))
    return texte, urls
