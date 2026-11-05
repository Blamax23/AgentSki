"""Teste la conversation avec Krok : mémoire séparée privé/groupe, résumés, plafond, adresse dans le groupe, /oublie, mode Achille.
Modèles simulés, base jetable. Lancer : .venv/bin/python scripts/test_conversation.py"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "conversation.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env
os.environ["OPENROUTER_API_KEY"] = "cle-de-test"
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from agentski import conversation  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Memoire, Personne, Reglage, Souvenir  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


appels = []
def generateur(systeme, messages):
    appels.append((systeme, messages))
    return "Salut, ça va ?"
def resumeur(systeme, utilisateur):
    return "RÉSUMÉ: Maxime aime les pistes noires.\nHUMEUR: content et un peu taquin"

with SessionLocal() as s:
    maxime = s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))

    # --- adresse dans le groupe ---
    verifie(conversation.adresse_a_krok("salut tout le monde", "krok_bot", False) is None, "groupe : sans interpellation, Krok se tait")
    verifie(conversation.adresse_a_krok("Krok t'es là ?", "krok_bot", False) == "Krok t'es là ?", "groupe : son nom suffit")
    verifie(conversation.adresse_a_krok("@krok_bot t'es là ?", "krok_bot", False) == "t'es là ?", "groupe : son @ est retiré du message")
    verifie(conversation.adresse_a_krok("ok merci", "krok_bot", True) == "ok merci", "groupe : répondre à un de ses messages compte")

    # --- privé : réponse, souvenirs, notice unique ---
    r1 = conversation.repondre(s, maxime, "Mon secret : j'adore les chamallows", "prive", False, generateur, resumeur)
    verifie(r1.startswith("Salut, ça va ?") and "/oublie" in r1, "privé : réponse + notice sur la mémoire la première fois")
    r2 = conversation.repondre(s, maxime, "Et toi ?", "prive", False, generateur, resumeur)
    verifie(r2 == "Salut, ça va ?", "privé : la notice n'est dite qu'une fois")
    verifie(s.scalar(select(func.count()).select_from(Souvenir)) == 4, "privé : les 2 échanges sont gardés (4 messages)")
    verifie(any("chamallows" in m["content"] for m in appels[-1][1]), "privé : l'historique est remis au modèle")

    # --- le groupe ne voit rien du privé ---
    conversation.repondre(s, maxime, "Krok, tu sais quoi sur moi ?", "groupe", False, generateur, resumeur)
    systeme, messages = appels[-1]
    verifie(not any("chamallows" in m["content"] for m in messages) and "chamallows" not in systeme, "groupe : aucune trace de l'historique privé")
    verifie("Maxime : Krok, tu sais quoi sur moi ?" in messages[-1]["content"], "groupe : le message est signé de son auteur")
    adrien_prive_avant = conversation._souvenirs(s, adrien, "prive")
    verifie(adrien_prive_avant == [], "privé : la conversation d'une personne n'est pas celle d'une autre")

    # --- résumé après RESUME_TOUS_LES échanges, et jamais mélangé entre lieux ---
    for i in range(conversation.RESUME_TOUS_LES):
        conversation.repondre(s, maxime, f"message {i}", "prive", False, generateur, resumeur)
    m = s.scalar(select(Memoire).where(Memoire.personne_id == maxime.id, Memoire.lieu == "prive"))
    verifie(m is not None and "pistes noires" in m.resume and m.depuis_resume < conversation.RESUME_TOUS_LES, "résumé : fiche privée mise à jour et compteur remis à zéro en cours de route")
    verifie(conversation.humeur(s) == "content et un peu taquin", "résumé : l'humeur de Krok est mise à jour")
    verifie(not s.scalar(select(Memoire).where(Memoire.personne_id == maxime.id, Memoire.lieu == "groupe")).resume, "résumé : la fiche du groupe n'a pas bougé")
    conversation.repondre(s, maxime, "encore", "prive", False, generateur, resumeur)
    verifie("pistes noires" in appels[-1][0], "privé : la fiche résumée est dans le contexte")
    conversation.repondre(s, maxime, "Krok ?", "groupe", False, generateur, resumeur)
    verifie("pistes noires" not in appels[-1][0], "groupe : la fiche privée n'est pas dans le contexte")

    # --- résumé en échec : la réponse part quand même ---
    def resumeur_casse(systeme, utilisateur):
        return "n'importe quoi"
    for i in range(conversation.RESUME_TOUS_LES):
        rep = conversation.repondre(s, adrien, f"coucou {i}", "prive", False, generateur, resumeur_casse)
    verifie(rep == "Salut, ça va ?", "résumé illisible : la conversation continue sans erreur")

    # --- mode Achille ---
    verifie(not conversation.mode_achille(s, 42), "Achille : désactivé par défaut")
    verifie(conversation.basculer_achille(s, 42) and conversation.mode_achille(s, 42), "Achille : /achille l'active")
    verifie(not conversation.mode_achille(s, 99), "Achille : propre à chaque conversation")
    verifie(conversation.basculer_achille(s, 42, False) is False and not conversation.mode_achille(s, 42), "Achille : /achille off le coupe")
    sys_achille, _ = conversation.composer(s, maxime, "yo", "prive", True)
    for attendu in ("cv cv", "collègue", "tia", "c't'ambiance", "-estre", "choquestre", "j'ai capté parfois", "première année d'alternance",
                    "Elisa", "la carte reste alléchante", "genre", "frérot", "frr", "frère", "Tavaillons", "JAMAIS", "AUCUN détail"):
        verifie(attendu in sys_achille, f"Achille : la consigne contient « {attendu} »")
    verifie("MODE ACHILLE" not in conversation.composer(s, maxime, "yo", "prive", False)[0], "Achille : absent du prompt quand le mode est coupé")

    # --- plafond quotidien ---
    s.add_all([Souvenir(personne_id=adrien.id, lieu="prive", role="user", texte="x", cree_a=conversation.maintenant()) for _ in range(conversation.LIMITE_PAR_JOUR)])
    s.commit()
    verifie("claqué" in conversation.repondre(s, adrien, "encore ?", "prive", False, generateur, resumeur), "plafond : au-delà de la limite, Krok se repose")

    # --- /oublie ---
    conversation.repondre(s, maxime, "Krok ?", "groupe", False, generateur, resumeur)
    texte = conversation.oublier(s, maxime)
    verifie("effacé" in texte, "oublie : confirmation")
    verifie(s.scalar(select(func.count()).select_from(Souvenir).where(Souvenir.personne_id == maxime.id)) == 0
            and s.scalar(select(func.count()).select_from(Memoire).where(Memoire.personne_id == maxime.id)) == 0, "oublie : souvenirs et fiches (privé et groupe) supprimés")
    verifie(s.scalar(select(func.count()).select_from(Souvenir).where(Souvenir.personne_id == adrien.id)) > 0, "oublie : les souvenirs des autres sont intacts")
    verifie("rien à effacer" in conversation.oublier(s, maxime), "oublie : rien à effacer la deuxième fois")
    r = conversation.repondre(s, maxime, "re-salut", "prive", False, generateur, resumeur)
    verifie("/oublie" in r, "oublie : la notice sur la mémoire revient après l'effacement")

print("Tout est bon.")
