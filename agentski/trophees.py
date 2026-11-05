"""Trophées cachés : ils se débloquent tout seuls, sans aucune saisie en plus, et sont annoncés dans le groupe.

Seuls les trophées dont la donnée existe sont calculés. Ceux de Garmin et de la position ne concernent que les personnes qui ont
des données et ne sont jamais comparés entre elles. Les badges illustrés (environ 120 images) ne sont pas générés : l'annonce est
du texte avec une médaille. Non calculables faute de donnée : Fermeture (la date de fin de saison n'est pas connue), Œil de lynx et
Doublure (aucun lien entre une descente filmée et un moment ou une personne à l'image), Citation culte, Pipelette, Le Muet et
Noctambule (les messages du groupe ne sont pas gardés), Collectionneur, Imitateur, Incompris, Quiz master et Oreille absolue
(aucun score de partie n'est gardé)."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .amis import _maintenant_aware, aujourdhui, groupe_id, reglage
from .entretien import _norm
from .horloge import maintenant, vers_paris
from .models import (Activite, Archive, ChampJournal, Chute, Citation, Defi, DefiVote, Descente, Idee, Journee, Morceau, Pari,
                     Personne, Presence, Reglage, Replique, Sequence, Trophee, Weekend)
from .surprises import Evenement

MEDAILLES = {1: "🥉", 2: "🥈", 3: "🥇"}
ANNONCES_PAR_PASSAGE = 3


# --- Données de base ----------------------------------------------------------------------------------------------

def _journees(session: Session, p: Personne) -> list[Journee]:
    return list(session.scalars(select(Journee).join(Presence, Presence.journee_id == Journee.id).where(
        Presence.personne_id == p.id, Journee.ski.is_(True)).order_by(Journee.jour)))


def _chutes_par_journee(session: Session, p: Personne) -> dict[int, int]:
    return dict(session.execute(select(Chute.journee_id, func.count()).where(Chute.personne_id == p.id).group_by(Chute.journee_id)).all())


def _plus_longue_suite(valeurs: list[int]) -> int:
    meilleur = courant = 0
    precedent = None
    for v in sorted(set(valeurs)):
        courant = courant + 1 if precedent is not None and v == precedent + 1 else 1
        meilleur, precedent = max(meilleur, courant), v
    return meilleur


def _semaine(d: date) -> int:
    """Numéro de semaine continu (le lundi de la semaine / 7) : deux semaines de suite valent n et n + 1, même autour du nouvel an."""
    return (d - timedelta(days=d.weekday())).toordinal() // 7


def _reglage_entier(session: Session, cle: str) -> int:
    return int(reglage(session, cle) or 0)


def _votes(session: Session, defi_id: int) -> list[int]:
    return list(session.scalars(select(DefiVote.valeur).where(DefiVote.defi_id == defi_id)))


def _defis_de(session: Session, p: Personne, *types: str) -> list[Defi]:
    return list(session.scalars(select(Defi).where(Defi.tire_id == p.id, Defi.type.in_(types)).order_by(Defi.id)))


# --- Calculs : chaque fonction renvoie un nombre (trophée à niveaux) ou un booléen ---------------------------------

def premier_sang(s, p):
    premiere = s.scalar(select(Chute.personne_id).order_by(Chute.id).limit(1))
    return premiere == p.id


def pingouin(s, p):
    return s.scalar(select(func.count()).select_from(Chute).where(Chute.personne_id == p.id))


def phenix(s, p):
    par_jour = _chutes_par_journee(s, p)
    jours = _journees(s, p)
    return any(par_jour.get(a.id, 0) >= 3 and par_jour.get(b.id, 0) == 0 for a, b in zip(jours, jours[1:]))


def chute_verte(s, p):
    return s.scalar(select(Chute.id).where(Chute.personne_id == p.id, Chute.verte.is_(True)).limit(1)) is not None


def double(s, p):
    return any(n >= 2 for n in _chutes_par_journee(s, p).values())


def weekend_blanc(s, p):
    for w in s.scalars(select(Weekend).where(Weekend.statut == "confirme", Weekend.date_fin < aujourdhui())):
        mes_jours = [j for j in _journees(s, p) if j.weekend_id == w.id]
        if len(mes_jours) >= 2 and not s.scalar(select(Chute.id).where(Chute.personne_id == p.id, Chute.journee_id.in_([j.id for j in mes_jours])).limit(1)):
            return True
    return False


def incassable(s, p):
    par_jour = _chutes_par_journee(s, p)
    suite = meilleure = 0
    for j in _journees(s, p):
        suite = suite + 1 if par_jour.get(j.id, 0) == 0 else 0
        meilleure = max(meilleure, suite)
    return meilleure >= 5


def tombe_premier_jour(s, p):
    premiere = s.scalar(select(Journee).where(Journee.ski.is_(True)).order_by(Journee.jour).limit(1))
    return premiere is not None and s.scalar(select(Chute.id).where(Chute.personne_id == p.id, Chute.journee_id == premiere.id).limit(1)) is not None


def premiere_trace(s, p):
    premiere = s.scalar(select(Journee).where(Journee.ski.is_(True)).order_by(Journee.jour).limit(1))
    return premiere is not None and any(j.id == premiere.id for j in _journees(s, p))


def fidele(s, p):
    return len(_journees(s, p))


def infatigable(s, p):
    return _plus_longue_suite([j.jour.toordinal() for j in _journees(s, p)])


def jamais_deux_sans_trois(s, p):
    return _plus_longue_suite([_semaine(j.jour) for j in _journees(s, p)]) >= 3


def revenant(s, p):
    jours = [j.jour for j in _journees(s, p)]
    return any((b - a).days > 30 for a, b in zip(jours, jours[1:]))


def invite_honneur(s, p):
    return bool(_journees(s, p))


def nouvel_an(s, p):
    return any((j.jour.month, j.jour.day) in ((12, 31), (1, 1)) for j in _journees(s, p))


def cameraman(s, p):
    return s.scalar(select(func.count()).select_from(Descente).where(Descente.filmeur_id == p.id))


def vedette(s, p):
    """Cité dans les moments marquants de trois week-ends de suite."""
    semaines = []
    for w in s.scalars(select(Weekend).where(Weekend.statut == "confirme")):
        ids = [j.id for j in w.journees]
        moments = " ".join(_norm(v) for v in s.scalars(select(ChampJournal.valeur).where(
            ChampJournal.journee_id.in_(ids), ChampJournal.cle == "moment", ChampJournal.supprime.is_(False))))
        if _norm(p.prenom) in moments:
            semaines.append(_semaine(w.date_debut))
    return _plus_longue_suite(semaines) >= 3


def poete(s, p):
    """Citations de la personne gardées avec /citation (la citation est rattachée à qui l'a dite)."""
    return s.scalar(select(func.count()).select_from(Citation).where(Citation.personne_id == p.id))


def idealiste(s, p):
    return s.scalar(select(func.count()).select_from(Idee).where(Idee.auteur_id == p.id))


def parieur(s, p):
    return s.scalar(select(func.count()).select_from(Pari).where(Pari.auteur_id == p.id))


def pari_tenu(s, p):
    return s.scalar(select(Pari.id).where(Pari.gagnant_id == p.id).limit(1)) is not None


def rendez_vous(s, p):
    return _reglage_entier(s, f"rdv_count:{p.id}")


def meteorologue(s, p):
    return s.scalar(select(func.count()).select_from(Reglage).where(Reglage.cle.like(f"pointmeteo_fait:{p.id}:%")))


def _reussis(s, p, *types):
    n = 0
    for d in _defis_de(s, p, *types):
        votes = _votes(s, d.id)
        n += sum(v == 1 for v in votes) > sum(v == 0 for v in votes)
    return n


def premier_defi(s, p):
    return _reussis(s, p, "phrase", "gage") >= 1


def gage(s, p):
    return _reussis(s, p, "gage")


def chef_etoile(s, p):
    notes = [v for d in _defis_de(s, p, "chef") for v in _votes(s, d.id)]
    return len(notes) >= 3 and sum(notes) / len(notes) >= 8


def dj_resident(s, p):
    defis = _defis_de(s, p, "dj")
    notes = [v for d in defis for v in _votes(s, d.id)]
    return len(defis) >= 3 and bool(notes) and sum(notes) / len(notes) >= 8


def debatteur(s, p):
    ids = [j.id for j in _journees(s, p)]
    return s.scalar(select(func.count()).select_from(Defi).where(Defi.type == "debat", Defi.etat == "clos", Defi.journee_id.in_(ids)))


def avocat_du_diable(s, p):
    for d in _defis_de(s, p, "proces"):
        votes = _votes(s, d.id)
        if sum(v == 0 for v in votes) > sum(v == 1 for v in votes):
            return True
    return False


def coupable(s, p):
    n = 0
    for d in _defis_de(s, p, "proces"):
        votes = _votes(s, d.id)
        n += sum(v == 1 for v in votes) > sum(v == 0 for v in votes)
    return n


def madame_irma(s, p):
    juste = 0
    for d in s.scalars(select(Defi).where(Defi.type == "pronostic", Defi.etat == "clos")):
        resultat = reglage(s, f"pronostic_resultat:{d.id}")
        mon_vote = s.scalar(select(DefiVote.valeur).where(DefiVote.defi_id == d.id, DefiVote.personne_id == p.id))
        juste += resultat is not None and mon_vote is not None and (mon_vote == 1) == (resultat == "oui")
    return juste


def archiviste(s, p):
    return s.scalar(select(func.count()).select_from(Archive).where(Archive.auteur_id == p.id, Archive.etape == "ok"))


def cible_favorite(s, p):
    return s.scalar(select(func.count()).select_from(Replique).join(Archive, Replique.archive_id == Archive.id).where(Archive.cible_id == p.id))


def chambreur(s, p):
    return _reglage_entier(s, f"chambre_count:{p.id}")


def melomane(s, p):
    return s.scalar(select(func.count()).select_from(Morceau).where(Morceau.personne_id == p.id))


def mono_titre(s, p):
    return _reglage_entier(s, f"monotitre:{p.id}") >= 1


def _par_jour(s, p, colonne):
    return s.execute(select(Activite.journee_id, func.sum(colonne)).where(Activite.personne_id == p.id).group_by(Activite.journee_id)).all()


def kilometre_vertical(s, p):
    return max((n or 0 for _, n in _par_jour(s, p, Activite.denivele_neg)), default=0)


def everest(s, p):
    return sum(n or 0 for _, n in _par_jour(s, p, Activite.denivele_neg)) >= 8849


def marathonien(s, p):
    return max((n or 0 for _, n in _par_jour(s, p, Activite.nb_descentes)), default=0)


def fusee(s, p):
    record = 0.0
    for a in s.scalars(select(Activite).where(Activite.personne_id == p.id, Activite.vitesse_max.is_not(None)).order_by(Activite.debut)):
        if record and a.vitesse_max > record:
            return True
        record = max(record, a.vitesse_max)
    return False


def leve_tot(s, p):
    return any(a.premiere_descente and vers_paris(a.premiere_descente).time() < time(9, 0)
               for a in s.scalars(select(Activite).where(Activite.personne_id == p.id)))


def dernier_remonte(s, p):
    return any(a.derniere_descente and vers_paris(a.derniere_descente).time() >= time(16, 30)
               for a in s.scalars(select(Activite).where(Activite.personne_id == p.id)))


def _champs_par_journee(s, p, cle):
    ids = [j.id for j in _journees(s, p)]
    resultat: dict[int, set[str]] = {}
    for jid, valeur in s.execute(select(ChampJournal.journee_id, ChampJournal.valeur).where(
            ChampJournal.journee_id.in_(ids), ChampJournal.cle == cle, ChampJournal.supprime.is_(False))):
        resultat.setdefault(jid, set()).add(valeur)
    return resultat


def grand_tour(s, p):
    return set().union(*_champs_par_journee(s, p, "station").values()) >= set(config.STATIONS) if _champs_par_journee(s, p, "station") else False


def explorateur(s, p):
    return any(v >= set(config.STATIONS) for v in _champs_par_journee(s, p, "station").values())


def la_cascade(s, p):
    return any(_norm(v) in ("cascades", "piste des cascades") for vs in _champs_par_journee(s, p, "piste").values() for v in vs)


def tout_en_haut(s, p):
    return any("grandes platieres" in _norm(v) for vs in _champs_par_journee(s, p, "piste").values() for v in vs)


# (code, nom, description, seuils bronze/argent/or ou None, fonction)
CATALOGUE = [
    ("premier_sang", "Premier sang", "la première chute de la saison du groupe", None, premier_sang),
    ("pingouin", "Pingouin", "chutes dans la saison", (5, 15, 30), pingouin),
    ("phenix", "Le Phénix", "trois chutes ou plus un jour, aucune le suivant", None, phenix),
    ("chute_verte", "Chute verte", "une chute sur une piste verte", None, chute_verte),
    ("double", "Doublé", "deux chutes dans la même journée", None, double),
    ("weekend_blanc", "Week-end blanc", "un week-end entier sans chute", None, weekend_blanc),
    ("incassable", "Incassable", "cinq jours de ski d'affilée sans chute", None, incassable),
    ("tombe_premier_jour", "Tombé le premier jour", "une chute dès le premier jour de la saison", None, tombe_premier_jour),
    ("premiere_trace", "Première trace", "le premier jour de ski de la saison", None, premiere_trace),
    ("fidele", "Fidèle", "jours de ski dans la saison", (10, 20, 30), fidele),
    ("infatigable", "Infatigable", "jours de ski d'affilée", (3, 5, 7), infatigable),
    ("jamais_deux", "Jamais deux sans trois", "trois week-ends de suite", None, jamais_deux_sans_trois),
    ("revenant", "Le revenant", "le retour après plus d'un mois sans ski", None, revenant),
    ("invite_honneur", "Invité d'honneur", "la première apparition dans la série", None, invite_honneur),
    ("nouvel_an", "Nouvel An sur les skis", "skier le 31 décembre ou le 1er janvier", None, nouvel_an),
    ("cameraman", "Caméraman", "descentes filmées dans la saison", (10, 20, 40), cameraman),
    ("vedette", "Vedette", "cité dans les moments marquants de trois week-ends de suite", None, vedette),
    ("poete", "Poète", "phrases cultes capturées avec /citation", (5, 15, 30), poete),
    ("idealiste", "Idéaliste", "idées ajoutées avec /idee", (5, 15, 30), idealiste),
    ("parieur", "Parieur", "paris enregistrés avec /pari", (3, 10, 20), parieur),
    ("pari_tenu", "Pari tenu", "un pari gagné", None, pari_tenu),
    ("rendez_vous", "Rendez-vous", "utilisations de /rdv", (5, 15, 30), rendez_vous),
    ("meteorologue", "Météorologue", "matins avec le point météo actif", (10, 30, 60), meteorologue),
    ("premier_defi", "Premier défi", "le premier défi réussi", None, premier_defi),
    ("gage", "Gagé", "gages accomplis", (5, 10, 20), gage),
    ("chef_etoile", "Chef étoilé", "une note moyenne de 8 sur 10 ou plus comme chef du soir", None, chef_etoile),
    ("dj_resident", "DJ résident", "DJ trois fois avec une note moyenne de 8 sur 10 ou plus", None, dj_resident),
    ("debatteur", "Débatteur", "débats du télésiège tranchés en sa présence", (5, 15, 30), debatteur),
    ("avocat_diable", "Avocat du diable", "acquitté au procès", None, avocat_du_diable),
    ("coupable", "Coupable", "condamnations au procès", (3, 5, 10), coupable),
    ("madame_irma", "Madame Irma", "pronostics justes", (3, 10, 20), madame_irma),
    ("archiviste", "Archiviste", "archives ajoutées à la réserve", (5, 15, 30), archiviste),
    ("cible_favorite", "Cible favorite", "fois visé par une archive", (5, 15, 30), cible_favorite),
    ("chambreur", "Chambreur", "fois où il a lancé /chambre", (10, 25, 50), chambreur),
    ("melomane", "Mélomane", "morceaux ajoutés à la playlist", (10, 25, 50), melomane),
    ("mono_titre", "Mono-titre", "le même morceau ajouté deux fois", None, mono_titre),
    ("kilometre_vertical", "Kilomètre vertical", "mètres de dénivelé négatif dans une journée", (1000, 2000, 3000), kilometre_vertical),
    ("everest", "Everest", "8 849 m de dénivelé négatif cumulés dans la saison", None, everest),
    ("marathonien", "Marathonien", "descentes dans une journée", (15, 30, 45), marathonien),
    ("fusee", "Fusée", "battre son record de vitesse de la saison", None, fusee),
    ("leve_tot", "Lève-tôt", "une première descente avant 9 h", None, leve_tot),
    ("dernier_remonte", "Dernier remonté", "une dernière descente après 16 h 30", None, dernier_remonte),
    ("grand_tour", "Grand tour", "passer par les cinq stations dans la saison", None, grand_tour),
    ("explorateur", "Explorateur", "passer par les cinq stations le même jour", None, explorateur),
    ("la_cascade", "La Cascade", "avoir fait la piste des Cascades", None, la_cascade),
    ("tout_en_haut", "Tout en haut", "être passé aux Grandes Platières", None, tout_en_haut),
]
VITRINE = (15, 30, 45)
NOMS = {code: nom for code, nom, *_ in CATALOGUE} | {"vitrine_pleine": "Vitrine pleine"}
TOTAL = len(CATALOGUE) + 1


def _niveau(valeur, seuils) -> int:
    if seuils is None:
        return 1 if valeur else 0
    return sum(1 for seuil in seuils if (valeur or 0) >= seuil)


def calculer(session: Session) -> int:
    """Débloque ce qui doit l'être pour tout le monde ; renvoie le nombre de nouveaux niveaux (annoncés plus tard)."""
    nouveaux = 0
    for p in session.scalars(select(Personne).order_by(Personne.id)):  # sans donnée, rien ne se débloque : pas besoin d'écarter qui que ce soit
        atteint = {code: _niveau(fonction(session, p), seuils) for code, _, _, seuils, fonction in CATALOGUE}
        deja = dict(session.execute(select(Trophee.code, func.max(Trophee.niveau)).where(Trophee.personne_id == p.id).group_by(Trophee.code)).all())
        for code, niveau in atteint.items():
            for n in range(deja.get(code, 0) + 1, niveau + 1):
                session.add(Trophee(personne_id=p.id, code=code, niveau=n, debloque_a=_maintenant_aware()))
                nouveaux += 1
        session.flush()
        codes = session.scalar(select(func.count(func.distinct(Trophee.code))).where(Trophee.personne_id == p.id, Trophee.code != "vitrine_pleine"))
        deja_vitrine = deja.get("vitrine_pleine", 0)
        for n in range(deja_vitrine + 1, _niveau(codes, VITRINE) + 1):
            session.add(Trophee(personne_id=p.id, code="vitrine_pleine", niveau=n, debloque_a=_maintenant_aware()))
            nouveaux += 1
    session.commit()
    return nouveaux


def _texte(session: Session, t: Trophee) -> str:
    personne = session.get(Personne, t.personne_id)
    if t.code == "vitrine_pleine":
        return f"🏆 {personne.prenom} débloque « Vitrine pleine » {MEDAILLES[t.niveau]} : {VITRINE[t.niveau - 1]} trophées différents !"
    _, nom, description, seuils, _ = next(c for c in CATALOGUE if c[0] == t.code)
    detail = f"{seuils[t.niveau - 1]:,} {description}".replace(",", " ") if seuils else description
    return f"🏆 {personne.prenom} débloque « {nom} »" + (f" {MEDAILLES[t.niveau]}" if seuils else "") + f" : {detail}"


def evenements(session: Session) -> list[Evenement]:
    """Toutes les cinq minutes : calcule, puis annonce au groupe trois trophées au plus par passage (pas de déluge au premier calcul)."""
    aujourd = aujourdhui()
    if session.scalar(select(Journee.id).where(Journee.ski.is_(True), Journee.jour >= aujourd - timedelta(days=1), Journee.jour <= aujourd).limit(1)) is None:
        return []  # ni calcul ni annonce hors d'un jour de ski (ou du lendemain) : la préparation, les archives et les tests ne débloquent rien
    derniere = reglage(session, "trophees_dernier")
    if derniere and maintenant() - datetime.fromisoformat(derniere) < timedelta(minutes=5):
        return []
    from .amis import definir_reglage
    definir_reglage(session, "trophees_dernier", maintenant().isoformat())
    calculer(session)
    groupe = groupe_id(session)
    orga = session.scalar(select(Personne).where(Personne.prenom == config.ORGANISATEUR))
    destinataire = "groupe" if groupe else (orga.telegram_id if orga else None)
    if destinataire is None:
        return []
    a_annoncer = list(session.scalars(select(Trophee).where(Trophee.annonce.is_(False)).order_by(Trophee.id).limit(ANNONCES_PAR_PASSAGE)))
    sortie = [Evenement(destinataire, _texte(session, t)) for t in a_annoncer]
    for t in a_annoncer:
        t.annonce = True
    session.commit()
    return sortie


def vitrine(session: Session, p: Personne) -> str:
    """Ses trophées débloqués ; les autres restent cachés, seul leur nombre est donné."""
    lignes = []
    for t in session.scalars(select(Trophee).where(Trophee.personne_id == p.id).order_by(Trophee.debloque_a, Trophee.id)):
        seuils = next((c[3] for c in CATALOGUE if c[0] == t.code), VITRINE)
        lignes.append(f"{MEDAILLES[t.niveau] if seuils else '🏅'} {NOMS[t.code]}")
    codes = session.scalar(select(func.count(func.distinct(Trophee.code))).where(Trophee.personne_id == p.id))
    if not lignes:
        return f"🏆 Aucun trophée débloqué pour l'instant, sur {TOTAL} à découvrir. Ils restent cachés jusqu'au déblocage."
    return f"🏆 Tes trophées : {codes} sur {TOTAL}, le reste est caché\n" + "\n".join(lignes)
