"""Teste l'entretien du soir de bout en bout, sans réseau : extraction simulée, base jetable.
Avec --reel : passe en plus les vrais vocaux du dossier Vocaux dans Voxtral et Haiku (coûte quelques millièmes de dollar).
Lancer : .venv/bin/python scripts/test_entretien.py [--reel]"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "entretien.db")
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from sqlalchemy import select  # noqa: E402

from agentski import config, entretien, horloge, journal, rappels  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes  # noqa: E402
from agentski.models import (Base, ChampJournal, Chute, Descente, Entretien, Journee, Personne, Presence, Rappel,  # noqa: E402
                             Transcription, Weekend)

Base.metadata.create_all(engine)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def extraction(texte, jour, pistes):
    """Faux modèle : repère quelques mots, comme le ferait le vrai, avec la phrase source."""
    d = {"stations": [], "pistes": [], "meteo": None, "neige": None, "ton": None, "moments": [], "chutes": []}
    if "Flaine" in texte:
        d["stations"].append({"valeur": "Flaine", "source": "à Flaine"})
    if "platières" in texte.lower():
        d["pistes"].append({"valeur": "Grandes Platières", "source": "grandes platières", "filmeur": "Adrien"})
    if "tête d'essai" in texte:
        d["pistes"].append({"valeur": "tête d'essai", "source": "tête d'essai", "filmeur": None})
    if "soleil" in texte:
        d["meteo"] = {"valeur": "Grand soleil", "source": "Grand soleil"}
    if "Adrien est tombé deux fois" in texte:
        d["chutes"].append({"personne": "Adrien", "nombre": 2, "source": "Adrien est tombé deux fois"})
    if "moment" in texte:
        d["moments"].append({"valeur": "Le moment du jour", "source": "un moment"})
    return d


def appuyer(session, message, libelle, eid_pers):
    """Appuie sur le bouton dont le libellé contient `libelle` ; renvoie la réaction."""
    data = next(d for ligne in message.boutons for t, d in ligne if libelle in t)
    return entretien.bouton(session, data, eid_pers)


with SessionLocal() as s:
    seed_personnes(s)
    maxime = s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    weekend = Weekend(date_debut=date(2026, 12, 12), date_fin=date(2026, 12, 13), statut="confirme")
    s.add(weekend)
    s.flush()
    j = Journee(weekend_id=weekend.id, jour=date(2026, 12, 12))
    s.add(j)
    s.flush()
    s.add_all([Presence(journee_id=j.id, personne_id=maxime.id), Presence(journee_id=j.id, personne_id=adrien.id)])
    s.commit()
    JID, MID, AID = j.id, maxime.id, adrien.id

# --- Parcours principal -----------------------------------------------------
with SessionLocal() as s:
    horloge.regler(horloge.vers_utc(__import__("datetime").datetime(2026, 12, 12, 19, 5)))
    soir = rappels.texte_soir(s, s.get(Journee, JID))
    verifie(any("e|go|" in d for ligne in soir.boutons for _, d in ligne), "le rappel du soir porte le bouton de l'entretien")

    ouverture = entretien.demarrer(s, JID, MID)[0]
    verifie("Raconte ta journée" in ouverture.texte and len(ouverture.boutons[0]) == 2, "ouverture : question ouverte + Passer / Plus tard")
    e = entretien.entretien_ouvert(s, MID)
    verifie(e is not None and e.attente == "vocal", "entretien en cours, en attente du vocal")

    texte = ("Samedi à Flaine avec Adrien. Première descente sur les grandes platières, "
             "tête d'essai ensuite. Adrien est tombé deux fois. Grand soleil.")
    msgs = entretien.recevoir_transcription(s, e, texte, 15.0, 0.001, extraction)
    verifie(s.scalar(select(Transcription).where(Transcription.entretien_id == e.id)).texte_brut == texte, "transcription brute conservée")
    faits = {(f.cle, f.valeur): f for f in s.scalars(select(ChampJournal).where(ChampJournal.entretien_id == e.id))}
    verifie(("station", "Flaine") in faits and faits[("station", "Flaine")].phrase_source == "à Flaine", "fait extrait avec sa phrase source")
    verifie(("piste", "Les Grandes Platières") in faits, "« Grandes Platières » reconnue comme la piste connue")
    verifie(("meteo", "Grand soleil") in faits, "météo dite dans le vocal : pas redemandée")
    verifie("Qui était là" in msgs[-1].texte and e.relances == 1, "première relance : qui était là (boutons)")
    verifie("✅ Adrien" in str(msgs[-1].boutons), "les présents de la veille sont pré-cochés")

    reaction = appuyer(s, msgs[-1], "Valider", MID)
    verifie("neige" in reaction.suite[0].texte.lower(), "présents confirmés ; station et météo déjà dites : on saute à la neige")
    verifie(len(s.scalars(select(Presence).where(Presence.journee_id == JID)).all()) == 2, "présences enregistrées")
    reaction = appuyer(s, reaction.suite[0], "Dure", MID)
    verifie("ton" in reaction.suite[0].texte.lower(), "puis le ton")
    reaction = appuyer(s, reaction.suite[0], "Rigolo", MID)
    verifie("tête d'essai" in reaction.suite[0].texte and "C'est quelle piste" in reaction.suite[0].texte, "piste inconnue : confirmation par boutons")
    reaction = appuyer(s, reaction.suite[0], "Tête des Saix", MID)
    verifie("marqué" in reaction.suite[0].texte, "puis le moment marquant (vocal)")
    verifie(s.get(Entretien, e.id).relances <= config.RELANCES_MAX, "au plus RELANCES_MAX relances")
    reaction = appuyer(s, reaction.suite[0], "Passer cette question", MID)
    ecran = reaction.suite[0]
    verifie("Des chutes" in ecran.texte and "Adrien ×2" in ecran.texte, "chutes : le total déjà dit dans le vocal est affiché")
    reaction = appuyer(s, ecran, "+1 Adrien", MID)
    verifie("Adrien ×3" in reaction.edition.texte, "+1 s'ajoute au total, sans doublon")
    verifie(s.scalar(select(Chute.id).where(Chute.journee_id == JID).limit(1)) is not None, "chutes stockées")
    reaction = appuyer(s, reaction.edition, "Terminé", MID)
    verifie("Journal du Samedi 12 décembre enregistré" in reaction.suite[0].texte, "entretien terminé")
    descentes = s.scalars(select(Descente)).all()
    verifie(len(descentes) == 2 and {d.piste for d in descentes} == {"Les Grandes Platières", "Tête des Saix"}, "descentes créées")
    verifie(any(d.filmeur_id == AID for d in descentes), "qui filmait est conservé par descente")
    verifie(s.get(Entretien, e.id).etat == "termine", "état : terminé")

    md = journal.journal_md(s, None)
    verifie("# Week-end du 12 au 13 décembre 2026" in md and "Adrien ×3" in md and "Flaine (#" in md, "/journal en Markdown")
    print("\n" + md + "\n")
    numero = journal.numero_cite("supprime #" + str(faits[("meteo", "Grand soleil")].id))
    verifie("Supprimé" in journal.supprimer(s, numero), "« supprime ça » efface un élément")
    verifie("Grand soleil" not in journal.journal_md(s, None), "l'élément supprimé n'apparaît plus")
    verifie(s.get(ChampJournal, numero).supprime, "…mais reste en base, marqué supprimé")
    verifie("clos" in entretien.demarrer(s, JID, MID)[0].texte, "un entretien terminé ne redémarre pas")

# --- Passer, plus tard, reprise, plafond de relances --------------------------
with SessionLocal() as s:
    j2 = Journee(weekend_id=weekend.id if False else s.scalar(select(Weekend.id)), jour=date(2026, 12, 13))
    s.add(j2)
    s.flush()
    s.add(Presence(journee_id=j2.id, personne_id=MID))
    s.commit()
    horloge.regler(horloge.vers_utc(__import__("datetime").datetime(2026, 12, 13, 19, 5)))
    entretien.demarrer(s, j2.id, MID)
    e2 = entretien.entretien_ouvert(s, MID)
    msgs2 = entretien.recevoir_transcription(s, e2, "rien de précis", None, None, extraction)
    reaction = appuyer(s, msgs2[-1], "Valider", MID)
    verifie("Où avez-vous skié" in reaction.suite[0].texte, "aucune station dite : question par boutons")
    reaction = appuyer(s, reaction.suite[0], "Valider", MID)
    verifie("Où avez-vous skié" in reaction.edition.texte, "valider sans station coché ne passe pas")
    reaction = appuyer(s, reaction.edition, "Morillon", MID)
    reaction = appuyer(s, reaction.edition, "Valider", MID)
    verifie(any(f.cle == "station" and f.valeur == "Morillon" and f.verifie for f in s.scalars(select(ChampJournal).where(ChampJournal.entretien_id == e2.id))),
            "station choisie par bouton enregistrée comme confirmée")
    reaction = entretien.bouton(s, f"e|{e2.id}|tard", MID)
    verifie("demain à 09:00" in reaction.edition.texte, "« plus tard » annonce la reprise du lendemain")
    reprise = s.scalar(select(Rappel).where(Rappel.type == "reprise"))
    verifie(horloge.vers_paris(reprise.envoyer_a).strftime("%d/%m %H:%M") == "14/12 09:00", "rappel de reprise le lendemain à 9 h")
    horloge.regler(horloge.vers_utc(__import__("datetime").datetime(2026, 12, 14, 9, 1)))
    envois = rappels.a_envoyer(s)
    verifie(any("resté en suspens" in x.message.texte for x in envois), "le lendemain, le bot propose de reprendre")
    bouton_reprendre = next(d for ligne in envois[0].message.boutons for t, d in ligne if "Reprendre" in t)
    reaction = entretien.bouton(s, bouton_reprendre, MID)
    verifie(s.get(Entretien, e2.id).etat == "en_cours" and reaction.suite, "reprise : l'entretien repart de là où il en était")
    reaction = entretien.bouton(s, f"e|{e2.id}|passer", MID)
    verifie(s.get(Entretien, e2.id).etat == "passe" and "gardé" in reaction.edition.texte, "« passer » clôt en gardant ce qui existe")

    # Plafond : avec RELANCES_MAX = 2, l'entretien saute directement aux chutes
    config.RELANCES_MAX = 2
    j3 = Journee(weekend_id=s.scalar(select(Weekend.id)), jour=date(2026, 12, 15))
    s.add(j3)
    s.flush()
    s.commit()
    entretien.demarrer(s, j3.id, MID)
    e3 = entretien.entretien_ouvert(s, MID)
    entretien.recevoir_transcription(s, e3, "rien de précis", None, None, extraction)
    s.refresh(e3)
    entretien.question_suivante(s, e3)
    verifie(e3.relances == 2, "relances plafonnées")
    verifie(entretien.question_suivante(s, e3).texte.startswith("Des chutes"), "plafond atteint : direction la question des chutes")

# --- Extraction qui échoue : le vocal est gardé ---------------------------------
with SessionLocal() as s:
    config.RELANCES_MAX = 7
    j4 = Journee(weekend_id=s.scalar(select(Weekend.id)), jour=date(2026, 12, 16))
    s.add(j4)
    s.commit()
    entretien.demarrer(s, j4.id, MID)
    e4 = entretien.entretien_ouvert(s, MID)

    def casse(*_):
        raise RuntimeError("réseau")
    msgs = entretien.recevoir_transcription(s, e4, "un vocal", None, None, casse)
    verifie("pas réussi à l'analyser" in msgs[0].texte and "Qui était là" in msgs[1].texte, "extraction en panne : message clair, on continue en boutons")
    verifie(s.scalar(select(Transcription).where(Transcription.entretien_id == e4.id)).texte_brut == "un vocal", "…et la transcription est gardée")

# --- Reconnaissance des pistes ---------------------------------------------------
verifie(entretien.reconnaitre_piste("tete des saix") == "Tête des Saix", "piste : accents et casse ignorés")
verifie(entretien.reconnaitre_piste("les cascades") == "Les Cascades", "piste : article ignoré")
verifie(entretien.reconnaitre_piste("tête d'essai") is None, "piste : un mot trop différent n'est pas deviné")

if "--reel" in sys.argv:
    from agentski import audio, ia
    print("\n--- Vrais vocaux ---")
    for fichier in sorted((RACINE / "Vocaux").glob("*.ogg")):
        with tempfile.TemporaryDirectory() as dossier:
            morceaux = audio.en_morceaux_mp3(fichier, Path(dossier))
            texte, cout = ia.transcrire(morceaux)
        donnees = ia.extraire(texte, "2026-12-12", entretien.pistes_connues())
        print(f"\n{fichier.name} ({cout:.4f} $)\n  texte : {texte}\n  stations : {[x['valeur'] for x in donnees['stations']]}"
              f"\n  pistes : {[(x['valeur'], x['filmeur']) for x in donnees['pistes']]}"
              f"\n  météo : {donnees['meteo']['valeur'] if donnees['meteo'] else None} · neige : "
              f"{donnees['neige']['valeur'] if donnees['neige'] else None}"
              f"\n  chutes : {[(x['personne'], x['nombre']) for x in donnees['chutes']]}"
              f"\n  moments : {[x['valeur'] for x in donnees['moments']]}")
print("\nTout passe.")
