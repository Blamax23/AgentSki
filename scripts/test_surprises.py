"""Teste les surprises : archives, répliques, commandes cachées, événements datés, couche Telegram.
Modèle créatif simulé, base jetable, horloge simulée. Lancer : .venv/bin/python scripts/test_surprises.py"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "surprises.db")
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, config, fun, horloge, surprises  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Archive, Base, ChampJournal, Chute, Journee, Moment, Personne, Reglage, Replique, Weekend  # noqa: E402

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


def a(annee, mois, jour, h=12, m=0):
    horloge.regler(horloge.vers_utc(datetime(annee, mois, jour, h, m)))


def p(session, prenom):
    return session.scalar(select(Personne).where(Personne.prenom == prenom))


compteur = {"n": 0}
def gen(systeme, utilisateur):
    compteur["n"] += 1
    return f"Réplique {compteur['n']} 🤣"


a(2026, 12, 12, 11)
# --- Ajout d'une archive -------------------------------------------------------------
with SessionLocal() as s:
    maxime, adr, achille = p(s, "Maxime"), p(s, "Adrien"), p(s, "Achille")
    verifie(surprises.tirer(s) is None, "réserve vide : rien à tirer")
    surprises.commencer_archive(s, maxime)
    arc = surprises.archive_en_attente(s, maxime, "media")
    verifie("Trop lourd" in surprises.recevoir_media(s, arc, "video", "V0", 30 * 1024 * 1024), "média de plus de 20 Mo refusé")
    ecran = surprises.recevoir_media(s, arc, "video", "V1", 5_000_000)
    verifie(any(f"x|{arc.id}|{adr.id}" in d for ligne in ecran.boutons for _, d in ligne), "puis « qui est visé ? » en boutons")
    verifie(surprises.choisir_cible(s, arc.id, adr.id, achille) is None, "un autre que l'auteur ne peut pas choisir la cible")
    question = surprises.choisir_cible(s, arc.id, adr.id, maxime)
    verifie("Adrien" in question, "cible choisie : on demande la description")
    surprises.enregistrer_message_archive(s, arc.id, 321)
    verifie(surprises.decrire(s, maxime, 999, "x") is None, "une réponse à un autre message n'est pas la description")
    verifie("🥚" in surprises.decrire(s, maxime, 321, "Il tombe sur une verte"), "description reçue : archive ajoutée")
    verifie(s.get(Archive, arc.id).etape == "ok", "l'archive est prête")
    surprises.commencer_archive(s, maxime)
    surprises.commencer_archive(s, maxime)
    verifie(len(s.scalars(select(Archive).where(Archive.etape != "ok")).all()) == 1, "une archive abandonnée est remplacée, pas empilée")
    s.delete(surprises.archive_en_attente(s, maxime, "media"))
    s.commit()
    # deuxième archive visant Achille, déjà envoyée une fois
    s.add(Archive(auteur_id=adr.id, cible_id=achille.id, type="photo", file_id="P1", description="Paires", etape="ok", nb_envois=1))
    s.commit()

    # --- Tirage et réplique --------------------------------------------------------------
    verifie(surprises.tirer(s).file_id == "V1", "le tirage préfère l'archive la moins souvent envoyée")
    verifie(surprises.tirer(s, "achille").file_id == "P1", "tirage sur une personne précise")
    verifie(surprises.tirer(s, "Zébulon") is None and surprises.tirer(s, "Icham") is None, "personne inconnue ou sans archive : rien")
    archive = surprises.tirer(s)
    l1 = surprises.improviser_ligne(s, archive, gen)
    verifie(s.get(Archive, archive.id).nb_envois == 1 and s.scalar(select(Replique)).texte == l1, "la réplique est gardée, le compteur monte")
    l2 = surprises.improviser_ligne(s, archive, lambda *_: l1)
    verifie(l2 != l1 and "ça ne s'oublie pas" in l2, "jamais deux fois la même ligne sur une archive : repli si le modèle se répète")
    l3 = surprises.improviser_ligne(s, archive, lambda *_: (_ for _ in ()).throw(RuntimeError("réseau")))
    verifie("ça ne s'oublie pas" in l3, "modèle en panne : ligne de secours")
    chambre = surprises.chambrer(s, "Achille", gen)
    verifie(chambre.media == ("photo", "P1") and "Réplique" in chambre.texte, "/chambre Achille : l'archive sur Achille")
    verifie("Aucune archive sur Icham" in surprises.chambrer(s, "Icham", gen), "/chambre sans archive : message clair")

    # --- Commandes cachées ----------------------------------------------------------------
    verifie("Aucune chute sur piste verte" in surprises.podium_verte(s), "/verte sans donnée : explication")
    amis.ajouter_chute(s, maxime, "Adrien", verte=True)
    amis.ajouter_chute(s, maxime, "Adrien", verte=True)
    amis.ajouter_chute(s, maxime, "Achille", verte=True)
    amis.ajouter_chute(s, maxime, "Maxime")
    podium = surprises.podium_verte(s)
    verifie(podium.index("🥇 Adrien : 2") < podium.index("🥈 Achille : 1") and "Maxime" not in podium, "/verte : podium des chutes déclarées sur verte")
    verifie("🧀" in surprises.fait_fromage(lambda *_: (_ for _ in ()).throw(RuntimeError("x"))), "/raclette : texte de secours")
    verifie(surprises.fait_fromage(lambda *_: "Fait absurde.") == "🧀 Fait absurde.", "/raclette : texte du modèle")
    verifie(surprises.parfois_blase("Chute notée.", gen, hasard=lambda: 0.5) == "Chute notée.", "moniteur blasé : presque jamais")
    verifie("Réplique" in surprises.parfois_blase("Chute notée.", gen, hasard=lambda: 0.001), "moniteur blasé : une fois sur cinquante")
    verifie(surprises.parfois_blase("Chute notée.", lambda *_: (_ for _ in ()).throw(RuntimeError()), hasard=lambda: 0.001) == "Chute notée.", "…jamais au prix de la réponse")
    verifie("14/03" in surprises.regler_anniversaire(s, achille, "14/03") and "Format" in surprises.regler_anniversaire(s, achille, "n'importe"), "/anniversaire")
    p(s, "Adrien").anniversaire = date(2000, 3, 14)
    s.commit()

# --- Événements du moment ---------------------------------------------------------------------
config.EASTER_PROBA, config.FLASH_PROBA = 1.0, 0.0
with SessionLocal() as s:
    a(2026, 12, 12, 17, 30)
    verifie(surprises.evenements(s, gen) == [], "pas de groupe enregistré : aucune surprise")
    amis.definir_reglage(s, "groupe_id", "-100")
    a(2026, 12, 20, 17, 30)
    verifie(surprises.evenements(s, gen) == [], "pas de journée de ski aujourd'hui : aucune surprise")
    a(2026, 12, 12, 17, 30)
    evs = surprises.evenements(s, gen)
    verifie(len(evs) == 1 and evs[0].destinataire == "groupe" and evs[0].media is not None, "journée de ski : une archive part au groupe")
    verifie(surprises.evenements(s, gen) == [], "…une seule fois dans la journée")

    config.EASTER_PROBA, config.FLASH_PROBA = 0.0, 1.0
    a(2026, 12, 12, 18, 30)
    s.delete(s.get(Reglage, "flash:2026-12-12"))  # le plan « pas de flash » du matin est retiré pour rejouer
    s.commit()
    evs = surprises.evenements(s, lambda *_: "⚠️ FLASH : Adrien aperçu sur une verte.")
    verifie(any(e.texte.startswith("⚠️ FLASH") and e.media is None for e in evs), "flash info : fausse dépêche au groupe")

    a(2027, 4, 1, 8, 59)
    verifie(surprises.evenements(s, gen) == [], "1er avril avant 9 h : rien")
    a(2027, 4, 1, 9, 5)
    evs = surprises.evenements(s, lambda *_: "Flaine ferme ses pistes ce week-end.")
    verifie(len(evs) == 1 and evs[0].texte.startswith("📰"), "1er avril : fausse nouvelle le matin")
    verifie(surprises.evenements(s, gen) == [], "…une seule fois")
    a(2027, 4, 1, 18, 5)
    evs = surprises.evenements(s, gen)
    verifie(any("Poisson d'avril" in e.texte for e in evs), "1er avril : la nouvelle est révélée en fin de journée")

    a(2027, 1, 1, 0, 5)
    evs = surprises.evenements(s, gen)
    verifie("Bonne année 2027" in evs[0].texte and 1 < len(evs) <= 4, "nouvel an : message et jusqu'à 3 archives")
    a(2027, 1, 1, 3, 0)
    verifie(not any("Bonne année" in e.texte for e in surprises.evenements(s, gen)), "nouvel an : pas envoyé si minuit est passé depuis plus de 2 h")

    j = s.scalar(select(Journee).where(Journee.jour == date(2026, 12, 12)))
    s.add(Moment(journee_id=j.id, personne_id=1, texte="Première trace sur la Timale"))
    s.commit()
    a(2027, 12, 12, 9, 30)
    evs = surprises.evenements(s, gen)
    verifie(any("Il y a un an" in e.texte and "Timale" in e.texte for e in evs), "il y a un an : un moment du journal ressorti")

    a(2027, 3, 14, 9, 5)
    evs = [e for e in surprises.evenements(s, gen) if isinstance(e.destinataire, int)]
    verifie(len(evs) == 2 or len(evs) == 1, "anniversaire : message privé à chaque personne concernée")
    adrien_msg = next(e for e in evs if e.destinataire == 2)
    verifie("Joyeux anniversaire Adrien" in adrien_msg.texte and adrien_msg.media is not None, "anniversaire : avec sa pire archive en cadeau")
    verifie(not [e for e in surprises.evenements(s, gen) if isinstance(e.destinataire, int)], "anniversaire : une seule fois par an")

# --- Couche Telegram ---------------------------------------------------------------------------------
def maj(uid, texte=None, args=(), chat="private", **extra):
    msg = SimpleNamespace(text=texte, reply_text=AsyncMock(), photo=extra.get("photo"), video=extra.get("video"),
                          animation=None, document=None, caption=None)
    ctx = SimpleNamespace(args=list(args), bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=800)),
                          send_photo=AsyncMock(), send_video=AsyncMock()), job_queue=SimpleNamespace(run_once=lambda *a, **k: a))
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=uid, first_name="T"), effective_chat=SimpleNamespace(id=77, type=chat)), ctx


async def telegram():
    up, ctx = maj(3, chat="group")
    await fun.commande_easter(up, ctx)
    verifie("en privé" in up.message.reply_text.call_args[0][0], "/easter dans un groupe : on renvoie en privé")
    up, ctx = maj(3)
    await fun.commande_easter(up, ctx)
    verifie("moins de 20 Mo" in up.message.reply_text.call_args[0][0], "/easter en privé : on demande le média")
    up, ctx = maj(3, video=SimpleNamespace(file_id="V9", file_size=1000))
    pris = await fun.media_pour_archive(up, ctx)
    verifie(pris and "Qui est visé" in up.message.reply_text.call_args[0][0], "le média attendu est pris pour l'archive")
    with SessionLocal() as s:
        archive_id = s.scalar(select(Archive.id).where(Archive.file_id == "V9"))
        adrien_id = p(s, "Adrien").id
    q = SimpleNamespace(data=f"x|{archive_id}|{adrien_id}", answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=77))
    up = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=3))
    await fun.bouton_cible(up, ctx)
    verifie(ctx.bot.send_message.await_count == 1, "cible choisie : question de description en réponse forcée")
    up, ctx = maj(3, video=SimpleNamespace(file_id="V10", file_size=1000))
    verifie(not await fun.media_pour_archive(up, ctx), "un média sans archive en cours n'est pas pris pour une archive")
    up, ctx = maj(3, "Il glisse")
    verifie(await fun.description_archive(up, ctx, 800) and "Archive ajoutée" in up.message.reply_text.call_args[0][0], "la description clôt l'archive")

    up, ctx = maj(1, args=["Achille"])
    fun.ia.creatif = gen
    await fun.commande_chambre(up, ctx)
    verifie(ctx.bot.send_photo.await_count == 1 and "Réplique" in ctx.bot.send_photo.call_args.kwargs["caption"], "/chambre envoie le média avec la ligne")
    up, ctx = maj(1, args=["Icham"])
    await fun.commande_chambre(up, ctx)
    verifie("Aucune archive" in up.message.reply_text.call_args[0][0], "/chambre sans archive")
    up, ctx = maj(1)
    await fun.commande_verte(up, ctx)
    verifie("🟢" in up.message.reply_text.call_args[0][0], "/verte")

    config.AIDE_NON_UNE_FOIS_SUR = 1
    appels = []
    ctx.job_queue = SimpleNamespace(run_once=lambda f, d: appels.append(d))
    await fun.commande_aide(up, ctx)
    verifie(up.message.reply_text.call_args[0][0] == "Non." and appels == [10], "/aide : « Non. » puis la vraie aide 10 s plus tard")
    config.AIDE_NON_UNE_FOIS_SUR = 10 ** 9
    await fun.commande_aide(up, ctx)
    verifie("/moment" in up.message.reply_text.call_args[0][0], "/aide : l'aide, la plupart du temps")

    ctx = SimpleNamespace(error=RuntimeError("x"), bot=SimpleNamespace(send_message=AsyncMock()))
    await fun.erreur(SimpleNamespace(effective_chat=SimpleNamespace(id=77)), ctx)
    verifie("Sur une verte" in ctx.bot.send_message.call_args[0][1], "erreur technique : le bot se chambre lui-même")
    await fun.erreur(None, ctx)

    fun._evenements = lambda: [surprises.Evenement("groupe", "Salut groupe"), surprises.Evenement(2, "Salut Adrien", ("photo", "F"))]
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock()))
    await fun.envoyer_evenements(ctx)
    verifie(ctx.bot.send_message.call_args[0][0] == -100 and ctx.bot.send_photo.call_args[0][0] == 2, "les événements partent au groupe ou en privé")

asyncio.run(telegram())
print("\nTout passe.")
