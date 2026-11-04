"""Lecture du journal en Markdown et suppression d'un élément (« supprime ça »)."""
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .entretien import _chutes_comptees  # noqa: F401  (comptage des chutes par personne)
from .models import ChampJournal, Chute, Entretien, Journee, Personne, Presence, Weekend
from .rappels import _jour_lisible, NOMS_MOIS

LIBELLES = {"station": "Stations", "piste": "Pistes", "meteo": "Météo", "neige": "Neige", "ton": "Ton",
            "filmeur": "Filmeur"}
ETATS = {"termine": "terminé", "passe": "passé", "reporte": "reporté", "en_cours": "en cours"}


def _fait(c: ChampJournal) -> str:
    return f"{c.valeur} (#{c.id})"


def _journee_md(session: Session, j: Journee) -> str:
    champs = list(session.scalars(select(ChampJournal).where(
        ChampJournal.journee_id == j.id, ChampJournal.supprime.is_(False)).order_by(ChampJournal.id)))
    par_cle: dict[str, list[ChampJournal]] = {}
    for c in champs:
        par_cle.setdefault(c.cle, []).append(c)
    lignes = [f"## {_jour_lisible(j.jour)}"]
    presents = session.scalars(select(Personne).join(Presence, Presence.personne_id == Personne.id).where(
        Presence.journee_id == j.id).order_by(Personne.id))
    lignes.append("**Présents** : " + (", ".join(p.prenom for p in presents) or "non renseignés"))
    for cle in ("station", "piste", "meteo", "neige", "ton", "filmeur"):
        if cle in par_cle:
            lignes.append(f"**{LIBELLES[cle]}** : " + ", ".join(_fait(c) for c in par_cle[cle]))
    if "moment" in par_cle:
        lignes.append("**Moments marquants**")
        lignes += [f"- {_fait(c)}" for c in par_cle["moment"]]
    chutes = session.execute(select(Personne.prenom, Chute.id).join(Chute, Chute.personne_id == Personne.id).where(
        Chute.journee_id == j.id)).all()
    if chutes:
        compte: dict[str, int] = {}
        for prenom, _ in chutes:
            compte[prenom] = compte.get(prenom, 0) + 1
        lignes.append("**Chutes** : " + ", ".join(f"{p} ×{n}" for p, n in compte.items()))
    entretien = session.scalar(select(Entretien).where(Entretien.journee_id == j.id).order_by(Entretien.id.desc()))
    lignes.append(f"_Entretien : {ETATS.get(entretien.etat, entretien.etat)}_" if entretien else "_Pas d'entretien_")
    return "\n".join(lignes)


def journal_md(session: Session, cible: date | None = None) -> str:
    """Le week-end contenant `cible`, ou le dernier week-end qui a une journée de ski."""
    if cible is not None:
        weekend = session.scalar(select(Weekend).where(Weekend.date_debut <= cible, Weekend.date_fin >= cible))
    else:
        weekend = session.scalar(select(Weekend).where(Weekend.statut == "confirme").order_by(Weekend.date_debut.desc()))
    if weekend is None:
        return "Aucun week-end dans le journal."
    journees = session.scalars(select(Journee).where(Journee.weekend_id == weekend.id, Journee.ski.is_(True)).order_by(Journee.jour))
    d1, d2 = weekend.date_debut, weekend.date_fin
    titre = f"# Week-end du {d1.day} au {d2.day} {NOMS_MOIS[d2.month - 1]} {d2.year}" if d1 != d2 else f"# {_jour_lisible(d1)}"
    return titre + "\n\n" + "\n\n".join(_journee_md(session, j) for j in journees)


def supprimer(session: Session, numero: int) -> str:
    """Efface un fait du journal (reste en base, marqué supprimé, pour pouvoir le retrouver)."""
    champ = session.get(ChampJournal, numero)
    if champ is None or champ.supprime:
        return f"Je ne trouve pas d'élément #{numero}."
    champ.supprime = True
    session.commit()
    return f"🗑 Supprimé : {LIBELLES.get(champ.cle, champ.cle)} — {champ.valeur} (#{numero})."


def numero_cite(texte: str) -> int | None:
    """Numéro d'élément cité dans un texte : « supprime #12 », « supprime 12 »."""
    trouve = re.search(r"#?(\d+)", texte)
    return int(trouve.group(1)) if trouve else None
