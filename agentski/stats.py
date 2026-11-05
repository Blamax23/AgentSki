"""Statistiques, fils en cours, pistes à faire, tour de filmer, chronos et export d'un week-end. Cœur indépendant de Telegram.

Règle du projet : seuls les repères que tout le monde peut produire sont comparés (chronos sur une même piste, note sur 10,
chutes rapportées aux jours de présence). Ni Garmin ni position ne servent jamais à classer les uns par rapport aux autres."""
import random
import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, defis, entretien
from .amis import _maintenant_aware, aujourdhui, journee_du_jour, personne_par_prenom
from .briefing import faits_recap
from .horloge import vers_paris
from .journal import journal_md
from .models import (ChampJournal, Chrono, Chute, Citation, Defi, Descente, Idee, Journee, Moment, Morceau, Note, Pari, Personne,
                     Presence, Sequence, Weekend)
from .rappels import NOMS_MOIS, _conseil_pov, _jour_lisible


def _s(n: float | int, un: str, plusieurs: str | None = None) -> str:
    return un if n < 2 else (plusieurs or un + "s")


def formater_temps(secondes: int) -> str:
    return f"{secondes // 60}:{secondes % 60:02d}"


def lire_temps(texte: str) -> int | None:
    """« 1:32 », « 92 », « 1m32 », « 1'32 » : renvoie des secondes, ou None si ce n'est pas un temps plausible (de 5 s à 30 min)."""
    t = texte.strip().lower().replace("min", ":").replace("m", ":").replace("'", ":").replace("s", "").rstrip(":")
    try:
        morceaux = [int(x) for x in t.split(":")]
    except ValueError:
        return None
    if len(morceaux) == 1:
        secondes = morceaux[0]
    elif len(morceaux) == 2 and 0 <= morceaux[1] < 60:
        secondes = morceaux[0] * 60 + morceaux[1]
    else:
        return None
    return secondes if 5 <= secondes <= 1800 else None


# --- Chronos ---------------------------------------------------------------------------------------------------

def ajouter_chrono(session: Session, auteur: Personne, args: list[str]) -> str:
    """/chrono Serpentine 1:32 : le dernier mot est le temps, le reste est la piste."""
    if len(args) < 2:
        return "Format : /chrono Serpentine 1:32"
    secondes, nom = lire_temps(args[-1]), " ".join(args[:-1])
    if secondes is None:
        return "Je ne comprends pas ce temps. Écris-le comme 1:32, 92 ou 1m32 (entre 5 secondes et 30 minutes)."
    piste = entretien.reconnaitre_piste(nom)
    if piste is None:
        return f"Je ne connais pas la piste « {nom} ». Écris-la comme dans /afaire, ou ajoute-la en la citant dans un entretien."
    journee = journee_du_jour(session, auteur)
    meilleur = session.scalar(select(func.min(Chrono.secondes)).where(Chrono.personne_id == auteur.id, Chrono.piste == piste))
    session.add(Chrono(personne_id=auteur.id, journee_id=journee.id, piste=piste, secondes=secondes, cree_a=_maintenant_aware()))
    session.commit()
    suite = "premier chrono sur cette piste" if meilleur is None else ("nouveau record personnel 🎉" if secondes < meilleur else f"record à battre : {formater_temps(meilleur)}")
    return f"⏱ {auteur.prenom} sur {piste} : {formater_temps(secondes)} ({suite})."


# --- /stats ----------------------------------------------------------------------------------------------------

def _journees_ski(session: Session, personne_id: int) -> list[Journee]:
    return list(session.scalars(select(Journee).join(Presence, Presence.journee_id == Journee.id).where(
        Presence.personne_id == personne_id, Journee.ski.is_(True)).order_by(Journee.jour)))


def stats_personne(session: Session, personne: Personne) -> str:
    """Ce qui est connu sans saisie en plus ; une statistique manquante n'apparaît pas."""
    jours = _journees_ski(session, personne.id)
    if not jours:
        return f"{personne.prenom} : aucune journée de ski enregistrée pour l'instant."
    ids = [j.id for j in jours]
    lignes = [f"📊 {personne.prenom}", f"• {len(jours)} jour{'s' if len(jours) > 1 else ''} de ski, du {_jour_lisible(jours[0].jour)} au {_jour_lisible(jours[-1].jour)}"]
    descentes = session.scalar(select(func.count()).select_from(Descente).join(Sequence, Descente.sequence_id == Sequence.id).where(Sequence.journee_id.in_(ids)))
    if descentes:
        lignes.append(f"• {descentes} descentes déclarées sur ses journées")
    chutes = session.scalar(select(func.count()).select_from(Chute).where(Chute.personne_id == personne.id))
    if chutes:
        lignes.append(f"• {chutes} chute{'s' if chutes > 1 else ''}, soit {chutes / len(jours):.1f} par jour de ski")
    notes = list(session.execute(select(Note.note, Journee.jour).join(Journee, Note.journee_id == Journee.id).where(
        Note.personne_id == personne.id).order_by(Journee.jour)).all())
    if notes:
        suite = f" (de {notes[0][0]} à {notes[-1][0]} entre la première et la dernière)" if len(notes) > 1 else ""
        lignes.append(f"• note moyenne {sum(n for n, _ in notes) / len(notes):.1f}/10 sur {len(notes)} jour{'s' if len(notes) > 1 else ''}{suite}")
    chronos = session.execute(select(Chrono.piste, func.min(Chrono.secondes), func.count()).where(Chrono.personne_id == personne.id).group_by(Chrono.piste)).all()
    for piste, meilleur, n in chronos:
        premier = session.scalar(select(Chrono.secondes).where(Chrono.personne_id == personne.id, Chrono.piste == piste).order_by(Chrono.id).limit(1))
        evolution = f", premier {formater_temps(premier)}" if n > 1 else ""
        lignes.append(f"• {piste} : meilleur chrono {formater_temps(meilleur)} ({n} relevé{'s' if n > 1 else ''}{evolution})")
    for extra in _stats_activites(session, personne, ids):
        lignes.append(extra)
    return "\n".join(lignes)


def _stats_activites(session: Session, personne: Personne, journee_ids: list[int]) -> list[str]:
    """Totaux des fichiers d'activité de la personne ; vide tant qu'aucun fichier n'a été envoyé."""
    try:
        from .activites import totaux
    except ImportError:
        return []
    return totaux(session, personne, journee_ids)


def stats_tous(session: Session) -> str:
    """Seulement les repères comparables : note sur 10, chutes rapportées aux jours de présence, chronos sur une même piste."""
    lignes = ["📊 Repères comparables (jamais de classement sur Garmin ni sur la position)"]
    for p in session.scalars(select(Personne).order_by(Personne.id)):
        jours = _journees_ski(session, p.id)
        if not jours:
            continue
        morceaux = [f"{len(jours)} jour{'s' if len(jours) > 1 else ''}"]
        chutes = session.scalar(select(func.count()).select_from(Chute).where(Chute.personne_id == p.id))
        if chutes:
            morceaux.append(f"{chutes / len(jours):.1f} chute{'s' if chutes / len(jours) >= 2 else ''} par jour")
        moyenne = session.scalar(select(func.avg(Note.note)).where(Note.personne_id == p.id))
        if moyenne is not None:
            morceaux.append(f"note {moyenne:.1f}/10")
        lignes.append(f"• {p.prenom} : " + " · ".join(morceaux))
    communes = session.execute(select(Chrono.piste).group_by(Chrono.piste).having(func.count(func.distinct(Chrono.personne_id)) >= 2)).scalars().all()
    for piste in communes:
        meilleurs = session.execute(select(Personne.prenom, func.min(Chrono.secondes)).join(Chrono, Chrono.personne_id == Personne.id).where(
            Chrono.piste == piste).group_by(Personne.prenom).order_by(func.min(Chrono.secondes))).all()
        lignes.append(f"⏱ {piste} : " + ", ".join(f"{p} {formater_temps(t)}" for p, t in meilleurs))
    return "\n".join(lignes) if len(lignes) > 1 else "Pas encore assez de données pour comparer."


# --- /fils, /afaire, /gopro -----------------------------------------------------------------------------------------

def fils(session: Session) -> str:
    lignes = ["🧵 Ce qui est en cours"]
    paris = list(session.scalars(select(Pari).where(Pari.statut == "ouvert").order_by(Pari.id)))
    if paris:
        lignes += ["🎲 Paris ouverts"] + [f"• #{p.id} {p.texte}" for p in paris]
    ouverts = list(session.scalars(select(Defi).where(Defi.etat == "en_cours", Defi.type.in_(["debat", "pronostic", "imitation"])).order_by(Defi.id)))
    if ouverts:
        lignes += ["🎯 Défis à clore"] + [f"• {defis.TYPES[d.type][0]} : {d.contenu.split(defis.SEPARATEUR)[0].splitlines()[0][:90]}" for d in ouverts]
    idees = session.scalar(select(func.count()).select_from(Idee).where(Idee.rappelee.is_(False)))
    if idees:
        lignes.append(f"💡 {idees} idée{'s' if idees > 1 else ''} pas encore ressortie{'s' if idees > 1 else ''}")
    return "\n".join(lignes) if len(lignes) > 1 else "Rien en cours : aucun pari ni défi ouvert, aucune idée en attente."


def afaire(session: Session) -> str:
    """Pistes et stations pas encore faites d'après le journal ; le détail complet n'est jamais déversé d'un coup."""
    connues = entretien.pistes_connues()
    faites = {entretien._norm(v) for v in session.scalars(select(ChampJournal.valeur).where(ChampJournal.cle == "piste", ChampJournal.supprime.is_(False)))}
    faites |= {entretien._norm(p) for p in session.scalars(select(Descente.piste).where(Descente.piste.is_not(None)))}
    restantes = [p for p in connues if entretien._norm(p) not in faites]
    visitees = {s for s in session.scalars(select(ChampJournal.valeur).where(ChampJournal.cle == "station", ChampJournal.supprime.is_(False)))}
    stations = [s for s in config.STATIONS if s not in visitees]
    lignes = [f"🗺 {len(connues) - len(restantes)} piste{'s' if len(connues) - len(restantes) > 1 else ''} faite{'s' if len(connues) - len(restantes) > 1 else ''} sur {len(connues)}"]
    if stations:
        lignes.append("Stations pas encore visitées : " + ", ".join(stations))
    if restantes:
        lignes.append("Quelques pistes à faire : " + ", ".join(random.sample(restantes, min(12, len(restantes)))))
    elif connues:
        lignes.append("Toutes les pistes connues sont faites. Bravo, ou la liste est trop courte 😏")
    return "\n".join(lignes)


def gopro(session: Session) -> str:
    """À qui le tour de filmer, d'après l'historique des descentes filmées."""
    return "🎥 " + _conseil_pov(session, defis.presents(session))


# --- /kit ------------------------------------------------------------------------------------------------------------------

def kit(session: Session, cible: date | None = None) -> tuple[str, str] | None:
    """Export Markdown de ce qui a été vécu : résumé, journal, citations, compteurs, notes, POV, moments, fils, morceaux.
    C'est de la matière brute, jamais un montage ni une voix off : le montage reste ton travail."""
    if cible is not None:
        weekend = session.scalar(select(Weekend).where(Weekend.date_debut <= cible, Weekend.date_fin >= cible))
    else:
        weekend = session.scalar(select(Weekend).where(Weekend.statut == "confirme").order_by(Weekend.date_debut.desc()))
    if weekend is None:
        return None
    ids = list(session.scalars(select(Journee.id).where(Journee.weekend_id == weekend.id, Journee.ski.is_(True))))
    if not ids:
        return None
    d1, d2 = weekend.date_debut, weekend.date_fin
    titre = f"# Kit du week-end du {d1.day} au {d2.day} {NOMS_MOIS[d2.month - 1]} {d2.year}" if d1 != d2 else f"# Kit du {_jour_lisible(d1)}"
    resume = [f.replace("calcul déjà fait : ", "") for f in faits_recap(session, weekend)]  # l'indice destiné au modèle n'a pas sa place ici
    corps = journal_md(session, d1).split("\n", 2)[2]  # le journal sans son titre ; chaque jour devient un sous-titre
    jours = "### " + corps.removeprefix("## ").replace("\n\n## ", "\n\n### ")
    parties = [titre, "## Résumé", "\n".join(f"- {f}" for f in resume), "## Journal", jours]

    def section(nom: str, lignes: list[str]) -> None:
        if lignes:
            parties.extend([f"## {nom}", "\n".join(lignes)])

    section("Citations", [f"- {session.get(Personne, c.personne_id).prenom} : « {c.texte} »" for c in session.scalars(
        select(Citation).where(Citation.journee_id.in_(ids)).order_by(Citation.id))])
    section("Compteurs de chutes", [f"- {prenom} : {n}" for prenom, n in session.execute(select(Personne.prenom, func.count()).select_from(Chute).join(
        Personne).where(Chute.journee_id.in_(ids)).group_by(Personne.prenom).order_by(func.count().desc())).all()])
    section("Notes et phrases du soir", [f"- {_jour_lisible(jour)} · {prenom} : {note}/10" + (f" — « {phrase} »" if phrase else "")
                                         for prenom, note, phrase, jour in session.execute(select(Personne.prenom, Note.note, Note.phrase, Journee.jour).join(
                                             Note, Note.personne_id == Personne.id).join(Journee, Note.journee_id == Journee.id).where(
                                             Note.journee_id.in_(ids)).order_by(Journee.jour, Personne.id)).all()])
    section("Points de vue (descentes filmées)", [f"- {prenom} : {n}" for prenom, n in session.execute(select(Personne.prenom, func.count()).select_from(
        Descente).join(Sequence, Descente.sequence_id == Sequence.id).join(Personne, Descente.filmeur_id == Personne.id).where(
        Sequence.journee_id.in_(ids)).group_by(Personne.prenom).order_by(func.count().desc())).all()])
    section("Marqueurs posés avec /moment", [f"- {vers_paris(m.cree_a.replace(tzinfo=None)):%a %d/%m %H:%M}" + (f" : {m.texte}" if m.texte else "")
                                             for m in session.scalars(select(Moment).where(Moment.journee_id.in_(ids)).order_by(Moment.cree_a))])
    section("Défis", [f"- {defis.TYPES[t][0]}" for t in session.scalars(select(Defi.type).where(Defi.journee_id.in_(ids)).order_by(Defi.id))])
    section("Morceaux ajoutés", [f"- {titre} — {artistes} (par {prenom})" for titre, artistes, prenom in session.execute(select(
        Morceau.titre, Morceau.artistes, Personne.prenom).join(Personne, Morceau.personne_id == Personne.id).where(
        Morceau.weekend_id == weekend.id).order_by(Morceau.id)).all()])
    return f"kit-{d1.isoformat()}.md", "\n\n".join(parties) + "\n"
