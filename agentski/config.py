import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")  # exigé au démarrage du bot, pas à l'import : les tests n'en ont pas besoin
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DATABASE_PATH = ROOT / os.environ.get("DATABASE_PATH", "data/agentski.db")
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)  # data/ n'est pas dans git : absent sur un PC neuf


# Les personnes de la série. L'organisateur reçoit la question du jeudi et les rappels.
PERSONNES = [("Maxime", "duo_saison"), ("Adrien", "duo_saison"), ("Achille", "ami"), ("Icham", "ami"), ("Alix", "hote")]
ORGANISATEUR = os.environ.get("ORGANISATEUR", "Maxime")

# Horaires des rappels, heure de Paris (proposés, en attente de validation)
HEURE_JEUDI = os.environ.get("HEURE_JEUDI", "18:00")
HEURE_MATIN = os.environ.get("HEURE_MATIN", "07:30")
HEURE_SOIR = os.environ.get("HEURE_SOIR", "19:00")
RETARD_MAX_MIN = int(os.environ.get("RETARD_MAX_MIN", "120"))

# Relances de la question du jeudi : toutes les 3 h, jamais la nuit, jusqu'au vendredi soir (à valider)
RELANCE_TOUTES_LES_H = int(os.environ.get("RELANCE_TOUTES_LES_H", "3"))
RELANCE_DE_H = int(os.environ.get("RELANCE_DE_H", "8"))
RELANCE_JUSQU_A_H = int(os.environ.get("RELANCE_JUSQU_A_H", "22"))


# Entretien du soir
MODELE_TRANSCRIPTION = os.environ.get("MODELE_TRANSCRIPTION", "mistralai/voxtral-small-transcribe-3-0")
MODELE_EXTRACTION = os.environ.get("MODELE_EXTRACTION", "anthropic/claude-haiku-5.5")
MODELE_CREATIF = os.environ.get("MODELE_CREATIF", "anthropic/claude-sonnet-5.5")  # easter eggs, flash infos, briefing
RELANCES_MAX = int(os.environ.get("RELANCES_MAX", "7"))  # la doc prévoit 6 à 8 ; la question des chutes est en plus
DUREE_MORCEAU_S = int(os.environ.get("DUREE_MORCEAU_S", "50"))  # les fournisseurs coupent à 60 s de traitement
HEURE_REPRISE = os.environ.get("HEURE_REPRISE", "09:00")
STATIONS = ["Flaine", "Les Carroz", "Morillon", "Samoëns", "Sixt"]
METEOS = ["☀️ Grand soleil", "⛅ Nuageux", "🌫 Brouillard", "🌨 Neige", "💨 Vent"]
NEIGES = ["❄️ Poudreuse", "🧊 Dure", "💧 Soupe", "🪨 Tôlée", "🔀 Variable"]
TONS = ["😂 Rigolo", "🤩 Épique", "😌 Tranquille", "😅 Galère", "😬 Tendu"]
FICHIER_PISTES = ROOT / "donnees" / "pistes.txt"  # pistes de départ, dans le dépôt
FICHIER_PISTES_APPRISES = ROOT / "data" / "pistes_apprises.txt"  # pistes ajoutées au fil des entretiens

# Surprises : probabilités par journée de ski, et rareté du moniteur blasé
EASTER_PROBA = float(os.environ.get("EASTER_PROBA", "0.6"))
FLASH_PROBA = float(os.environ.get("FLASH_PROBA", "0.4"))
BLASE_UNE_FOIS_SUR = int(os.environ.get("BLASE_UNE_FOIS_SUR", "50"))
AIDE_NON_UNE_FOIS_SUR = int(os.environ.get("AIDE_NON_UNE_FOIS_SUR", "5"))

# Connexions : renseignées dans .env, absentes = fonction désactivée avec un message clair
AGENDA_ICAL_URL = os.environ.get("AGENDA_ICAL_URL", "")  # lien iCal privé en lecture de l'agenda de Maxime
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

# Question d'accès : dans .env, jamais dans le code. Sans réponse configurée, les inconnus sont ignorés.
QUESTION_ACCES = os.environ.get("QUESTION_ACCES", "")
REPONSE_ACCES = os.environ.get("REPONSE_ACCES", "")
ESSAIS_MAX = int(os.environ.get("ESSAIS_MAX", "3"))
BLOCAGE_MIN = int(os.environ.get("BLOCAGE_MIN", "60"))


def allowed_users() -> dict[int, str]:
    """ALLOWED_USERS="123:Maxime,456:Adrien" -> {123: "Maxime", 456: "Adrien"}"""
    users = {}
    for item in os.environ.get("ALLOWED_USERS", "").split(","):
        if ":" in item:
            tid, prenom = item.split(":", 1)
            users[int(tid)] = prenom.strip()
    return users
