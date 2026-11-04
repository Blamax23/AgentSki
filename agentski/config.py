import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DATABASE_PATH = ROOT / os.environ.get("DATABASE_PATH", "data/agentski.db")


def allowed_users() -> dict[int, str]:
    """ALLOWED_USERS="123:Maxime,456:Adrien" -> {123: "Maxime", 456: "Adrien"}"""
    users = {}
    for item in os.environ.get("ALLOWED_USERS", "").split(","):
        if ":" in item:
            tid, prenom = item.split(":", 1)
            users[int(tid)] = prenom.strip()
    return users
