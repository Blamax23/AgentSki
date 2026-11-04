"""Conversion des vocaux Telegram (OGG Opus, absent des formats d'OpenRouter) en MP3 et découpage en morceaux courts."""
import subprocess
from pathlib import Path

from . import config


def en_morceaux_mp3(source: Path, dossier: Path) -> list[Path]:
    """Convertit en MP3 mono 16 kHz et coupe tous les DUREE_MORCEAU_S secondes. Les fichiers sont dans `dossier`."""
    sortie = dossier / "morceau_%03d.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-ar", "16000", "-ac", "1", "-b:a", "64k",
                    "-f", "segment", "-segment_time", str(config.DUREE_MORCEAU_S), str(sortie)], check=True)
    return sorted(dossier.glob("morceau_*.mp3"))
