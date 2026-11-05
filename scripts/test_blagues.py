"""Teste /preaud, /icham, /adrille_rallye, /duel et /quicestce. Modèle simulé, base jetable.
Lancer : .venv/bin/python scripts/test_blagues.py"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "blagues.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import blagues, commandes_blagues, ia  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Citation, Journee, Personne, Weekend  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    for prenom, tid in (("Adrien", 2), ("Achille", 3)):
        s.scalar(select(Personne).where(Personne.prenom == prenom)).telegram_id = tid
    s.commit()


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


# --- /preaud --------------------------------------------------------------------------------------------------
lignes = blagues.PREAUD.split("\n")
verifie(lignes[0] == "Moi, Krok le bot, voici mon classement des Preaud préférés : ", "/preaud : la première phrase demandée, mot pour mot")
verifie(lignes[1:] == ["1. Roland", "2. Patrick", "3. Marion", "4. La PS4 d'Achille", "5. Alix", "6. Hercule Preaud (le futur fils d'Achille)", "7. Préau de ma cour d'école", "8. Achille"], "/preaud : la PS4 au-dessus d'Alix, puis Hercule et le préau entre Alix et Achille")

# --- /adrille_rallye ----------------------------------------------------------------------------------------------
pubs = {blagues.pub_adrille_rallye() for _ in range(60)}
verifie(all("https://adrille-rallye.vercel.app" in p for p in pubs), "pub : le lien est toujours là")
verifie(len(pubs) > 1, "pub : l'accroche varie d'une fois sur l'autre")
verifie(all("vidéos montées de rallyes et de courses de côte" in p and "quelques jours après chaque épreuve" in p for p in pubs), "pub : ne dit que ce que dit le site (vidéos montées, délai après l'épreuve)")
verifie(all("Krok" in p or "pub" in p.lower() or "annonce" in p.lower() or "rallye" in p.lower() for p in pubs), "pub : le bot s'exprime")

# --- /icham --------------------------------------------------------------------------------------------------------
vus = {}
def traducteur(systeme, utilisateur):
    vus["systeme"], vus["utilisateur"] = systeme, utilisateur
    return "  مرحبا يا صديقي  "
verifie("Format" in blagues.traduire_arabe("   ", traducteur), "/icham sans phrase : le format")
verifie(blagues.traduire_arabe("Salut mon pote", traducteur) == "مرحبا يا صديقي", "/icham : la traduction, rien d'autre, sans espaces autour")
verifie(vus["utilisateur"] == "Salut mon pote" and "arabe" in vus["systeme"] and "uniquement par la traduction" in vus["systeme"], "/icham : le modèle reçoit la phrase telle quelle et la consigne de ne rien ajouter")
verifie("n'arrive pas à traduire" in blagues.traduire_arabe("x", lambda *_: (_ for _ in ()).throw(OSError("réseau"))), "/icham : modèle en panne, message clair")

# --- /duel ----------------------------------------------------------------------------------------------------------
def recit(systeme, utilisateur):
    vus["duel"] = (systeme, utilisateur)
    return "Un concours de grimaces d'une intensité rare."
with SessionLocal() as s:
    t = blagues.duel(s, ["adrien", "ACHILLE"], recit)
    gagnant = t.split("Vainqueur : ")[1]
    verifie(t.startswith("⚔️ Adrien contre Achille") and gagnant in ("Adrien", "Achille") and "grimaces" in t, "/duel A B : l'affiche, le récit et un vainqueur")
    verifie(f"Vainqueur imposé : {gagnant}" in vus["duel"][1] and "ne le changes pas" in vus["duel"][0], "/duel : le vainqueur tiré par le code est imposé au modèle")
    verifie("sans aucun rapport avec le ski" in vus["duel"][0], "/duel : la discipline inventée n'a aucun rapport avec le ski")
    gains = {"Adrien": 0, "Achille": 0}
    for _ in range(300):
        gains[blagues.duel(s, ["Adrien", "Achille"], recit).split("Vainqueur : ")[1]] += 1
    verifie(min(gains.values()) >= 100, f"/duel : le tirage est équitable ({gains})")
    t = blagues.duel(s, ["Adrien"], recit)
    verifie("Adrien" in t.split("\n")[0] and t.split("\n")[0].count(" contre ") == 1, "/duel avec un seul nom : l'adversaire est tiré au sort")
    t = blagues.duel(s, [], recit)
    verifie("contre" in t and "Vainqueur" in t, "/duel sans nom : deux personnes au hasard")
    verifie("Je ne connais pas « Zébulon »" in blagues.duel(s, ["Adrien", "Zébulon"], recit), "/duel avec un inconnu : refus clair")
    verifie("deux personnes : pas plus" in blagues.duel(s, ["Adrien", "Achille", "Maxime"], recit), "/duel à trois : refus clair")
    t = blagues.duel(s, ["Adrien", "Achille"], lambda *_: (_ for _ in ()).throw(OSError("réseau")))
    verifie("Vainqueur" in t and "⚔️" in t, "/duel : modèle en panne, le résultat tombe quand même")

    # --- /quicestce ---------------------------------------------------------------------------------------------------
    verifie("Aucune citation" in blagues.nouvelle_devinette(s), "/quicestce sans citation : message clair")
    maxime, adr, ach = (s.scalar(select(Personne).where(Personne.prenom == p)) for p in ("Maxime", "Adrien", "Achille"))
    w = Weekend(date_debut=date(2026, 12, 12), date_fin=date(2026, 12, 12), statut="confirme")
    s.add(w)
    s.flush()
    j = Journee(weekend_id=w.id, jour=date(2026, 12, 12))
    s.add(j)
    s.flush()
    s.add(Citation(journee_id=j.id, personne_id=adr.id, texte="C'est une verte non ?"))
    s.commit()
    did, texte, boutons = blagues.nouvelle_devinette(s)
    verifie("C'est une verte non ?" in texte and "Adrien" not in texte, "/quicestce : la citation sans son auteur")
    verifie([b[0][0] for b in boutons] == ["Maxime", "Adrien", "Achille", "Icham", "Alix", "💡 Révéler"], "/quicestce : un bouton par personne et un pour révéler")
    texte2, boutons2 = blagues.repondre(s, did, maxime, str(adr.id))
    verifie("Ont répondu : Maxime" in texte2 and "C'était" not in texte2 and "✅" not in texte2 and "❌" not in texte2, "une réponse n'est pas jugée tout de suite : on ne révèle pas aux autres")
    texte3, _ = blagues.repondre(s, did, ach, str(maxime.id))
    verifie("Ont répondu : Maxime, Achille" in texte3, "les noms de ceux qui ont répondu s'accumulent")
    texte4, _ = blagues.repondre(s, did, maxime, str(ach.id))
    verifie(texte4.count("Maxime") == 1, "on ne change pas sa réponse en cliquant à nouveau")
    final, boutons_final = blagues.repondre(s, did, adr, "r")
    verifie("C'était Adrien" in final and "Bien vu : Maxime" in final and "Achille" not in final.split("Bien vu")[1] and boutons_final is None, "« Révéler » : l'auteur, ceux qui ont trouvé, plus de boutons")
    verifie(blagues.repondre(s, did, maxime, str(adr.id)) is None, "une question révélée est close")

# --- Couche Telegram ----------------------------------------------------------------------------------------------------
async def telegram():
    ia.economique = lambda *a, **k: "ترجمة"
    ia.creatif = lambda *a, **k: "Un duel de pierre-feuille-ciseaux."
    def maj(args=()):
        return SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(type="supergroup")), SimpleNamespace(args=list(args))
    up, ctx = maj()
    await commandes_blagues.commande_preaud(up, ctx)
    verifie(up.message.reply_text.call_args[0][0].startswith("Moi, Krok le bot"), "/preaud depuis Telegram")
    up, ctx = maj(["Bonjour", "tout", "le", "monde"])
    await commandes_blagues.commande_icham(up, ctx)
    verifie(up.message.reply_text.call_args[0][0] == "ترجمة", "/icham depuis Telegram")
    up, ctx = maj()
    await commandes_blagues.commande_adrille_rallye(up, ctx)
    verifie("adrille-rallye.vercel.app" in up.message.reply_text.call_args[0][0], "/adrille_rallye depuis Telegram")
    up, ctx = maj(["Adrien", "Achille"])
    await commandes_blagues.commande_duel(up, ctx)
    verifie("Vainqueur" in up.message.reply_text.call_args[0][0], "/duel depuis Telegram")
    up, ctx = maj()
    await commandes_blagues.commande_quicestce(up, ctx)
    verifie("Qui est-ce" in up.message.reply_text.call_args[0][0] and up.message.reply_text.call_args.kwargs["reply_markup"] is not None, "/quicestce depuis Telegram : question et boutons")
    with SessionLocal() as s:
        did = s.scalar(select(blagues.Devinette.id).order_by(blagues.Devinette.id.desc()))
    q = SimpleNamespace(data=f"q|{did}|1", answer=AsyncMock(), edit_message_text=AsyncMock())
    await commandes_blagues.bouton_quicestce(SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1)), SimpleNamespace())
    verifie("Ont répondu : Maxime" in q.edit_message_text.call_args[0][0], "un bouton de réponse met le message à jour")
    q = SimpleNamespace(data="q|999|r", answer=AsyncMock(), edit_message_text=AsyncMock())
    await commandes_blagues.bouton_quicestce(SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1)), SimpleNamespace())
    verifie(q.answer.call_args.kwargs.get("show_alert"), "une question inexistante ou close : message visible, rien ne change")

asyncio.run(telegram())
print("\nTout passe.")
