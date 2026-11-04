import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DATABASE_PATH = ROOT / os.environ.get("DATABASE_PATH", "data/agentski.db")


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
