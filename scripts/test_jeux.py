"""Teste idées, paris, courses, suggestions et défis (cœur puis couche Telegram), modèle simulé, base jetable.
Lancer : .venv/bin/python scripts/test_jeux.py"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "jeux.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from agentski import amis, defis, horloge, jeux, plans, rappels  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import (Archive, Base, ChampJournal, Chute, Defi, DefiVote, Element, Idee, Journee, Pari,  # noqa: E402
                             Personne, Plat, Reglage)

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


def p(session, prenom):
    return session.scalar(select(Personne).where(Personne.prenom == prenom))


horloge.regler(horloge.vers_utc(datetime(2026, 12, 12, 10, 0)))
vus = {}
def gen(systeme, utilisateur):
    vus["systeme"], vus["utilisateur"] = systeme, utilisateur
    return "Élément inventé"
def panne(*_):
    raise RuntimeError("réseau")

with SessionLocal() as s:
    maxime, adr, achille = p(s, "Maxime"), p(s, "Adrien"), p(s, "Achille")
    j = amis.journee_du_jour(s, maxime)
    amis._presence(s, j, adr)
    amis._presence(s, j, achille)
    s.commit()

    # --- Idées, paris ------------------------------------------------------------------------
    verifie("Format" in plans.ajouter_idee(s, maxime, "  "), "/idee vide : le format est rappelé")
    plans.ajouter_idee(s, maxime, "Soirée raclette et cartes")
    plans.ajouter_idee(s, adr, "Descente aux flambeaux")
    plans.ajouter_idee(s, adr, "Luge de nuit")
    plans.ajouter_idee(s, adr, "Spa")
    verifie(plans.idees_pour_rappel(s, 3) == ["Soirée raclette et cartes", "Descente aux flambeaux", "Luge de nuit"], "les plus anciennes idées ressortent d'abord")
    verifie(plans.idees_pour_rappel(s, 3) == ["Spa"] and plans.idees_pour_rappel(s) == [], "chaque idée n'est reprise qu'une fois")
    plans.ajouter_idee(s, maxime, "Patinoire")
    matin = rappels.texte_matin(s, j)
    verifie("Patinoire" in matin.texte and "Idées de la boîte" in matin.texte, "le rappel du matin reprend les idées")

    verifie("Aucun pari" in plans.paris_ouverts(s), "aucun pari au départ")
    verifie("#1" in plans.ajouter_pari(s, maxime, "Adrien ne tombe pas"), "/pari")
    plans.ajouter_pari(s, adr, "Achille oublie ses gants")
    verifie("#1" in plans.paris_ouverts(s) and "#2" in plans.paris_ouverts(s), "/paris liste les paris ouverts")
    verifie("Adrien gagne" in plans.resoudre_pari(s, 2, "adrien"), "/resolu tranche un pari")
    verifie("#2" not in plans.paris_ouverts(s) and "Pas de pari ouvert" in plans.resoudre_pari(s, 2, "Adrien"), "un pari tranché sort de la liste, et ne se retranche pas")
    verifie("Je ne connais pas" in plans.resoudre_pari(s, 1, "Zébulon"), "gagnant inconnu : message clair")

    # --- Courses --------------------------------------------------------------------------------
    verifie("Aucun plat" in plans.liste_courses(s, gen), "courses : rien à calculer sans plat")
    verifie(plans.nombre_de_presents(s) == 3, "le nombre de présents vient de la journée du jour")
    plans.ajouter_plat(s, maxime, "raclette sans charcuterie")
    plans.ajouter_plat(s, adr, "tartiflette")
    liste = plans.liste_courses(s, lambda syst, user: (vus.update(u=user) or "- Reblochon : 1 kg"))
    verifie("3 personnes" in liste and "Reblochon" in liste, "/course : calcul pour le nombre de présents, estimé")
    verifie("raclette sans charcuterie" in vus["u"] and "tartiflette" in vus["u"] and "Nombre de personnes : 3" in vus["u"], "tous les plats partent dans une seule demande, avec les précisions")
    verifie("pas réussi" in plans.liste_courses(s, panne), "modèle en panne : message clair, plats gardés")
    verifie(s.scalar(select(Plat.id)) is not None, "…les plats restent")
    plans.vider_courses(s)
    verifie("Aucun plat" in plans.liste_courses(s, gen), "/course vider")

    # --- Suggestions ------------------------------------------------------------------------------
    verifie(plans.anonymiser(s, "une soirée pour Adrien et maxime") == "une soirée pour un ami et un ami", "les prénoms sont retirés avant la recherche web")
    reponse = plans.suggestion(s, "soirée pour Adrien", lambda d: (vus.update(d=d) or ("Idée 1", ["https://exemple.fr/a"])))
    verifie("Adrien" not in vus["d"] and "https://exemple.fr/a" in reponse, "la demande part sans prénom, les sources sont citées")
    verifie("n'a pas abouti" in plans.suggestion(s, "", panne), "recherche en panne : message clair")

    # --- Réserve des défis ---------------------------------------------------------------------------
    verifie("refusé" in defis.ajouter_element(s, maxime, "gage", "Skier les yeux fermés dans la descente", lambda *_: "REFUS : pendant la descente"), "gage jugé dangereux : refusé avec la raison")
    verifie("pendant la descente" in defis.ajouter_element(s, maxime, "gage", "Skier les yeux fermés", lambda *_: "REFUS : pendant la descente"), "…l'explication est donnée")
    verifie("refusé" in defis.ajouter_element(s, maxime, "gage", "Descendre avec un bandeau sur les yeux", panne), "juge en panne : la liste de mots interdits prend le relais")
    verifie("✅" in defis.ajouter_element(s, maxime, "gage", "Chanter la Marseillaise au restaurant", lambda *_: "OK"), "gage accepté")
    verifie("Je n'ai pas compris" in defis.ajouter_element(s, maxime, "blind", "x"), "réserve inexistante : refus")
    for texte in ("Chanter en yaourt", "Parler en rimes"):
        defis.ajouter_element(s, adr, "gage", texte, lambda *_: "OK")
    verifie("Marseillaise" in defis.mes_elements(s, maxime) and "yaourt" not in defis.mes_elements(s, maxime), "/mesajouts : seulement les siens")
    verifie("Pas d'ajout" in defis.retirer_element(s, maxime, s.scalar(select(Element.id).where(Element.auteur_id == adr.id))), "on ne retire pas l'ajout d'un autre")

    # --- Lancer les défis ------------------------------------------------------------------------------
    tires = []
    for _ in range(12):
        a = defis.lancer(s, "gage", gen)
        d = s.get(Defi, a.defi_id)
        el = next(e for e in s.scalars(select(Element).where(Element.type == "gage", Element.utilise.is_(True))) if e.texte in a.texte)
        assert el.auteur_id != d.tire_id or el.auteur_id is None, "la personne tirée a reçu sa propre proposition"
        tires.append(d.tire_id)
    verifie(all(a != b for a, b in zip(tires, tires[1:])), "12 tirages : jamais deux fois de suite la même personne, jamais sa propre proposition")
    verifie("Le groupe valide" in a.texte and len(a.boutons[0]) == 2, "gage : annonce avec validation par le groupe")
    for e in s.scalars(select(Element).where(Element.type == "gage")):
        e.utilise = True
    s.commit()
    avant = len(s.scalars(select(Element).where(Element.type == "gage")).all())
    defis.lancer(s, "gage", gen)
    verifie(len(s.scalars(select(Element).where(Element.type == "gage")).all()) == avant, "réserve épuisée : les éléments redeviennent disponibles")
    for e in list(s.scalars(select(Element).where(Element.type == "phrase"))):
        s.delete(e)
    s.commit()
    a = defis.lancer(s, "phrase", gen)
    verifie("Élément inventé" in a.texte and s.scalar(select(Element).where(Element.type == "phrase")).auteur_id is None, "réserve vide : le bot invente un élément")
    verifie("absurde" in vus["utilisateur"], "…avec la consigne du défi")

    a = defis.lancer(s, "chef", gen)
    verifie("cuisines" in a.texte and len(a.boutons) == 2 and s.scalar(select(Plat).where(Plat.texte == "Élément inventé")) is not None, "chef du soir : plat tiré et ajouté à la liste de courses")
    chef = s.get(Defi, a.defi_id)
    defi_id = chef.id
    tire = s.get(Personne, chef.tire_id)
    autre = adr if tire.id != adr.id else achille
    verifie(defis.voter(s, defi_id, tire, "8") is None, "on ne note pas son propre défi")
    m = defis.voter(s, defi_id, autre, "8")
    verifie("8.0/10 (1 note)" in m.texte, "note enregistrée, moyenne affichée")
    defis.voter(s, defi_id, autre, "6")
    verifie("6.0/10 (1 note)" in defis.voter(s, defi_id, autre, "6").texte, "changer sa note remplace la précédente")

    a = defis.lancer(s, "dj", gen)
    verifie("musique" in a.texte and len(a.boutons) == 2, "DJ : notes des autres")

    s.add(Archive(auteur_id=adr.id, cible_id=achille.id, type="photo", file_id="PX", description="Ses gants oubliés", etape="ok"))
    amis.ajouter_chute(s, maxime, "Adrien")
    s.commit()
    vu_proces = {"archive": False, "chute": False}
    for _ in range(40):
        a = defis.lancer(s, "proces", gen)
        vu_proces["archive"] |= a.media is not None
        vu_proces["chute"] |= "une chute le" in a.texte
        if all(vu_proces.values()):
            break
    verifie(vu_proces["archive"] and vu_proces["chute"], "procès : une archive ou une chute comme pièce à conviction")
    verifie(a.boutons[0][0][0].startswith("🔨"), "procès : le groupe vote coupable ou innocent")

    d1 = defis.lancer(s, "debat", gen)
    verifie("/verdict" in d1.texte and not d1.boutons, "débat du télésiège : pas de bouton, un verdict à noter")
    verifie("Aucun débat" in defis.verdict(s, "") or "vide" in defis.verdict(s, ""), "verdict vide refusé")
    verifie("Verdict noté" in defis.verdict(s, "La raclette gagne"), "/verdict note la décision")
    verifie(s.scalar(select(ChampJournal).where(ChampJournal.cle == "debat")).valeur.endswith("→ La raclette gagne"), "…au journal, avec la question")
    verifie("Aucun débat" in defis.verdict(s, "Encore"), "un débat tranché ne se retranche pas")

    q = defis.lancer(s, "quiz", lambda *_: "Quelle est la plus haute piste ? ||| Les Grandes Platières")
    verifie("Quelle est la plus haute piste" in q.texte and "Grandes Platières" not in q.texte, "quiz : la réponse n'est pas dans l'annonce")
    rev = defis.voter(s, q.defi_id, maxime, "r")
    verifie("Les Grandes Platières" in rev.texte, "quiz : la réponse se révèle au bouton")
    verifie("1 ont trouvé" in defis.voter(s, q.defi_id, maxime, "1").texte, "quiz : « j'avais bon » compte un point")
    s.add(Element(type="quiz", auteur_id=adr.id, texte="Sans réponse"))
    for e in s.scalars(select(Element).where(Element.type == "quiz", Element.auteur_id.is_(None))):
        e.utilise = True
    s.commit()

    pr = defis.lancer(s, "pronostic", gen)
    verifie({t for t, _ in pr.prives} == {1, 2, 3}, "pronostic : une question privée à chaque présent qui a un compte")
    defis.voter(s, pr.defi_id, adr, "1")
    defis.voter(s, pr.defi_id, achille, "0")
    res = defis.resultat_pronostic(s, True)
    verifie("✅ Juste : Adrien" in res and "❌ Raté : Achille" in res, "/resultat oui : qui avait raison")
    verifie("Aucun pronostic" in defis.resultat_pronostic(s, True), "un pronostic clos ne se rejoue pas")

    im = defis.lancer(s, "imitation", gen)
    cibles = {v.personne_id: v.valeur for v in s.scalars(select(DefiVote).where(DefiVote.defi_id == im.defi_id))}
    verifie(len(cibles) == 3 and all(i != c for i, c in cibles.items()), "imitation : jamais soi-même")
    verifie(len(im.prives) == 3 and all("imiteras" in m.texte for _, m in im.prives), "imitation : cible envoyée en privé")
    verifie("imitait" in defis.reveler_imitation(s) and "Aucune imitation" in defis.reveler_imitation(s), "imitation : le récap révèle qui imitait qui, une fois")
    verifie("/son" in defis.lancer(s, "blind", gen), "blind test sans morceaux : on explique comment en ajouter (le jeu complet est testé avec Spotify)")

# --- Couche Telegram ---------------------------------------------------------------------------------------
def maj(uid, texte=None, args=(), chat="private"):
    msg = SimpleNamespace(text=texte, reply_text=AsyncMock())
    ctx = SimpleNamespace(args=list(args), bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=950)),
                          send_photo=AsyncMock()))
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=uid), effective_chat=SimpleNamespace(id=77, type=chat)), ctx


def bouton(uid, data):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=77))
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=uid)), q


async def telegram():
    jeux.ia.creatif = lambda *a, **k: "OK"
    jeux.ia.economique = lambda syst, *a, **k: ("NON" if "blague" in a[0] else "OUI") if syst == plans.CONSIGNE_PLAT else "OK"
    jeux.ia.recherche_web = lambda d, **k: ("Idée trouvée", ["https://exemple.fr/x"])
    up, ctx = maj(1)
    await jeux.commande_defi(up, ctx)
    verifie(len(up.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard) == 10, "/defi : les dix défis en boutons")

    with SessionLocal() as s:
        amis.definir_reglage(s, "groupe_id", "-100")
        for e in s.scalars(select(Element).where(Element.type == "phrase")):
            e.utilise = False
        s.commit()
    up, q = bouton(1, "df|phrase")
    ctx = maj(1)[1]
    await jeux.bouton_lancer(up, ctx)
    verifie(ctx.bot.send_message.call_args[0][0] == -100 and "phrase imposée" in ctx.bot.send_message.call_args[0][1], "défi lancé : annoncé dans le groupe")
    with SessionLocal() as s:
        defi = s.scalar(select(Defi).where(Defi.type == "phrase").order_by(Defi.id.desc()))
        tire, defi_id = defi.tire_id, defi.id
    up, q = bouton(tire, f"d|{defi_id}|1")
    await jeux.bouton_vote(up, maj(1)[1])
    verifie(q.answer.call_args.kwargs.get("show_alert"), "voter pour son propre défi : refus visible")
    autre = next(t for t in (1, 2, 3) if t != tire)
    up, q = bouton(autre, f"d|{defi_id}|1")
    await jeux.bouton_vote(up, maj(1)[1])
    verifie("✅ 1" in q.edit_message_text.call_args[0][0], "un vote met le message à jour")

    up, q = bouton(1, "df|blind")
    await jeux.bouton_lancer(up, maj(1)[1])
    verifie("/son" in q.edit_message_text.call_args[0][0], "blind test impossible : explication, rien n'est annoncé")

    up, ctx = maj(2, chat="group")
    await jeux.commande_ajout(up, ctx)
    verifie("en privé" in up.message.reply_text.call_args[0][0], "/ajout dans un groupe : on renvoie en privé")
    up, q = bouton(2, "a|gage")
    ctx = maj(2)[1]
    await jeux.bouton_ajout(up, ctx)
    verifie(ctx.bot.send_message.await_count == 1, "choix de la réserve : question en réponse forcée")
    up, ctx = maj(2, "Danser sur la table")
    verifie(await jeux.reponse_ajout(up, ctx, 950), "la réponse est prise comme élément")
    verifie("✅" in up.message.reply_text.call_args[0][0], "élément ajouté, anonymat rappelé")
    with SessionLocal() as s:
        verifie(s.get(Reglage, "ajout:950") is None, "la demande en attente est clôturée")
    up, ctx = maj(3, "Autre chose")
    verifie(not await jeux.reponse_ajout(up, ctx, 950), "la réponse d'un autre n'est pas prise")

    up, ctx = maj(1, args=["raclette", "sans", "charcuterie"])
    await jeux.commande_course(up, ctx)
    verifie("Courses pour" in up.message.reply_text.call_args[0][0], "/course plat : la liste de courses revient directement")
    up, ctx = maj(1, args=["blague", "nulle"])
    await jeux.commande_course(up, ctx)
    verifie("ne ressemble pas à un plat" in up.message.reply_text.call_args[0][0], "/course : ce qui n'est pas un plat est refusé")
    with SessionLocal() as s:
        verifie(s.scalar(select(func.count()).select_from(Plat).where(Plat.texte == "blague nulle")) == 0, "…et n'entre pas dans la liste")
    up, ctx = maj(1)
    await jeux.commande_course(up, ctx)
    verifie("Courses pour" in up.message.reply_text.call_args[0][0], "/course : la liste")
    from agentski import commandes_conversation, conversation as _conv
    with SessionLocal() as s:
        _conv.basculer_achille(s, 77, True)
    up, ctx = maj(1)
    up.effective_chat = SimpleNamespace(id=77, type="supergroup")
    await commandes_conversation.commande_krok(up, ctx)
    with SessionLocal() as s:
        verifie(not _conv.mode_achille(s, 77) and "Mode Achille coupé" in up.message.reply_text.call_args[0][0], "/krok coupe le mode Achille")
    up, ctx = maj(1, args=["thème"])
    await jeux.commande_suggestion(up, ctx)
    verifie("exemple.fr" in up.message.reply_text.call_args[0][0], "/suggestion : réponse avec ses sources")
    up, ctx = maj(1, args=["Soirée", "cartes"])
    await jeux.commande_idee(up, ctx)
    verifie("Idée" in up.message.reply_text.call_args[0][0], "/idee")
    up, ctx = maj(1, args=["Adrien", "gagne"])
    await jeux.commande_pari(up, ctx)
    up, ctx = maj(1, args=["x"])
    await jeux.commande_resolu(up, ctx)
    verifie("Format" in up.message.reply_text.call_args[0][0], "/resolu mal formé : le format est rappelé")
    up, ctx = maj(1, args=["peut-être"])
    await jeux.commande_resultat(up, ctx)
    verifie("Format" in up.message.reply_text.call_args[0][0], "/resultat mal formé : le format est rappelé")

asyncio.run(telegram())
print("\nTout passe.")
