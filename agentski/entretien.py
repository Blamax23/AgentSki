"""Entretien du soir : un vocal d'ouverture, puis au plus quelques relances sur ce qui manque, puis la question des chutes.

Cœur indépendant de Telegram : il lit et écrit la base, et renvoie des messages abstraits (texte + boutons).
L'extraction par modèle de langage est passée en paramètre, ce qui permet de tout tester sans réseau.
Callbacks : e|go|<journee> · e|<id>|passer · tard · reprendre · pres|<masque> · presok|<masque> · stat|<masque> ·
statok|<masque> · meteo|<i> · neige|<i> · ton|<i> · film|<i> · piste|<champ>|<j> · skipm · chute|<personne> · chutefin
"""
import difflib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import config
from .horloge import maintenant, vers_paris, vers_utc
from .models import ChampJournal, Chute, Descente, Entretien, Journee, Personne, Presence, Rappel, Sequence, Transcription
from .rappels import Message, _de, _jour_lisible

FILMEURS = ["Maxime", "Adrien", "Les deux", "Personne"]


@dataclass
class Reaction:
    """Réponse à un bouton : le message cliqué est réécrit (`edition`), puis les messages de `suite` sont envoyés."""
    edition: Message | None = None
    suite: list[Message] = field(default_factory=list)


# --- Pistes -----------------------------------------------------------------

def pistes_connues() -> list[str]:
    if not config.FICHIER_PISTES.exists():
        return []
    return [l.strip() for l in config.FICHIER_PISTES.read_text(encoding="utf-8").splitlines() if l.strip()]


def _norm(texte: str) -> str:
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")
    mots = sans_accents.lower().replace("'", " ").split()
    return " ".join(m for m in mots if m not in {"le", "la", "les", "l", "des", "du", "de"})


def reconnaitre_piste(nom: str) -> str | None:
    """Nom de la piste connue qui correspond (à l'accent, l'article ou une faute près), sinon None."""
    cible = _norm(nom)
    for connue in pistes_connues():
        if _norm(connue) == cible or difflib.SequenceMatcher(None, _norm(connue), cible).ratio() >= 0.85:
            return connue
    return None


def reconnaitre_station(nom: str) -> str | None:
    for s in config.STATIONS:
        if _norm(s) == _norm(nom):
            return s
    return None


# --- Accès aux faits --------------------------------------------------------

def _faits(session: Session, e: Entretien, cle: str) -> list[ChampJournal]:
    return list(session.scalars(select(ChampJournal).where(
        ChampJournal.entretien_id == e.id, ChampJournal.cle == cle, ChampJournal.supprime.is_(False))
        .order_by(ChampJournal.id)))


def _ajouter(session: Session, e: Entretien, cle: str, valeur: str, source: str, detail: str | None = None,
             verifie: bool = False, transcription_id: int | None = None) -> ChampJournal:
    champ = ChampJournal(journee_id=e.journee_id, entretien_id=e.id, transcription_id=transcription_id, cle=cle,
                         valeur=valeur, phrase_source=source, detail=detail, verifie=verifie)
    session.add(champ)
    return champ


def _bouton(session: Session, e: Entretien, cle: str, valeur: str, detail: str | None = None) -> None:
    _ajouter(session, e, cle, valeur, "bouton", detail, verifie=True)


def _journee(session: Session, e: Entretien) -> Journee:
    return session.get(Journee, e.journee_id)


def _organisateur(session: Session) -> Personne:
    return session.scalar(select(Personne).where(Personne.prenom == config.ORGANISATEUR))


def _ids_presents(session: Session, e: Entretien) -> list[int]:
    return [p.personne_id for p in session.scalars(select(Presence).where(Presence.journee_id == e.journee_id))]


# --- Démarrage, passer, plus tard -------------------------------------------

def entretien_ouvert(session: Session, personne_id: int) -> Entretien | None:
    return session.scalar(select(Entretien).where(Entretien.personne_id == personne_id, Entretien.etat == "en_cours")
                          .order_by(Entretien.id.desc()))


def _boutons_sortie(e: Entretien) -> list[tuple[str, str]]:
    return [("Passer", f"e|{e.id}|passer"), ("Plus tard", f"e|{e.id}|tard")]


def demarrer(session: Session, journee_id: int, personne_id: int) -> list[Message]:
    e = session.scalar(select(Entretien).where(Entretien.journee_id == journee_id, Entretien.personne_id == personne_id))
    if e is None:
        e = Entretien(journee_id=journee_id, personne_id=personne_id)
        session.add(e)
        session.commit()
    elif e.etat in ("termine", "passe"):
        return [Message("Cet entretien est déjà clos. /journal pour le relire.")]
    else:
        e.etat = "en_cours"
        session.commit()
        if e.attente != "vocal":
            return [Message("On reprend là où on s'était arrêtés."), question_suivante(session, e, compter=False)]
    jour = _jour_lisible(_journee(session, e).jour)
    return [Message(f"🎙 Entretien du soir — {jour}.\nRaconte ta journée en vocal : où vous êtes allés, ce qui s'est "
                    "passé, ce qui t'a marqué. Je poserai quelques questions sur ce qui manque.", [_boutons_sortie(e)])]


def passer(session: Session, e: Entretien) -> Message:
    e.etat, e.attente = "passe", "fini"
    session.commit()
    return Message("👌 On laisse l'entretien d'aujourd'hui. Ce qui était déjà noté est gardé.")


def plus_tard(session: Session, e: Entretien) -> Message:
    e.etat = "reporte"
    demain = vers_paris(maintenant()).date() + timedelta(days=1)
    h, m = config.HEURE_REPRISE.split(":")
    session.add(Rappel(destinataire_id=e.personne_id, type="reprise", journee_id=e.journee_id,
                       envoyer_a=vers_utc(datetime.combine(demain, time(int(h), int(m))))))
    session.commit()
    return Message(f"👌 On reprend demain à {config.HEURE_REPRISE}, là où on s'est arrêtés.")


def message_reprise(session: Session, journee_id: int, personne_id: int) -> Message | None:
    """Texte du rappel de reprise ; None si l'entretien n'est plus à reprendre."""
    e = session.scalar(select(Entretien).where(Entretien.journee_id == journee_id, Entretien.personne_id == personne_id,
                                               Entretien.etat == "reporte"))
    if e is None:
        return None
    jour = _jour_lisible(_journee(session, e).jour)
    return Message(f"☀️ L'entretien du {jour} est resté en suspens. On reprend ?",
                   [[("Reprendre", f"e|{e.id}|reprendre"), ("Passer", f"e|{e.id}|passer")]])


# --- Vocal reçu --------------------------------------------------------------

def recevoir_transcription(session: Session, e: Entretien, texte: str, duree_s: float | None, cout: float | None,
                           extracteur) -> list[Message]:
    """Garde la transcription brute, extrait les faits de tout l'entretien, puis passe à la question suivante."""
    t = Transcription(entretien_id=e.id, texte_brut=texte, duree_s=duree_s, cout=cout)
    session.add(t)
    session.commit()
    cumul = " ".join(x.texte_brut for x in session.scalars(
        select(Transcription).where(Transcription.entretien_id == e.id).order_by(Transcription.id)))
    prefixe = []
    try:
        donnees = extracteur(cumul, _journee(session, e).jour.isoformat(), pistes_connues())
        _enregistrer_extraction(session, e, t, donnees)
    except Exception as erreur:  # le vocal est gardé : on continue avec des boutons
        session.rollback()
        prefixe = [Message("⚠️ J'ai bien gardé ton vocal mais je n'ai pas réussi à l'analyser. "
                           "Je continue avec des questions à boutons.")]
        print(f"Extraction échouée : {erreur}")
    return prefixe + [question_suivante(session, e)]


def _enregistrer_extraction(session: Session, e: Entretien, t: Transcription, d: dict) -> None:
    # Les faits déjà confirmés par bouton, ou supprimés par l'utilisateur, ne sont jamais réécrits
    session.execute(delete(ChampJournal).where(
        ChampJournal.entretien_id == e.id, ChampJournal.verifie.is_(False), ChampJournal.supprime.is_(False),
        ChampJournal.cle.in_(["station", "piste", "meteo", "neige", "ton", "moment", "chute_vocal"])))

    def ajouter(cle, valeur, source, detail=None):
        _ajouter(session, e, cle, valeur, source, detail, transcription_id=t.id)

    for x in d["stations"]:
        ajouter("station", reconnaitre_station(x["valeur"]) or x["valeur"], x["source"])
    for x in d["pistes"]:
        ajouter("piste", reconnaitre_piste(x["valeur"]) or x["valeur"], x["source"], x["filmeur"])
    for cle in ("meteo", "neige", "ton"):
        if d[cle]:
            ajouter(cle, d[cle]["valeur"], d[cle]["source"])
    for x in d["moments"]:
        ajouter("moment", x["valeur"], x["source"])
    for x in d["chutes"]:
        ajouter("chute_vocal", x["personne"], x["source"], str(x["nombre"]))
    session.commit()


# --- Questions ---------------------------------------------------------------

def _poser(session: Session, e: Entretien, attente: str, message: Message, compter: bool) -> Message:
    e.attente = attente
    if compter and attente != "chutes":
        e.relances += 1
    session.commit()
    return message


def question_suivante(session: Session, e: Entretien, compter: bool = True) -> Message:
    """La plus utile des questions encore sans réponse ; toujours la question des chutes en dernier."""
    if e.relances < config.RELANCES_MAX:
        if not _faits(session, e, "presents_ok"):
            return _poser(session, e, "presents", _ecran_presents(session, e, None), compter)
        if not _faits(session, e, "station"):
            return _poser(session, e, "stations", _ecran_stations(e, 0), compter)
        if not _faits(session, e, "meteo"):
            return _poser(session, e, "meteo", _ecran_choix(e, "meteo", "Quel temps aujourd'hui ?", config.METEOS), compter)
        if not _faits(session, e, "neige"):
            return _poser(session, e, "neige", _ecran_choix(e, "neige", "Et la neige ?", config.NEIGES), compter)
        if not _faits(session, e, "ton"):
            return _poser(session, e, "ton", _ecran_choix(e, "ton", "Le ton de la journée ?", config.TONS), compter)
        pistes = _faits(session, e, "piste")
        if pistes and not _faits(session, e, "filmeur") and not any(p.detail for p in pistes):
            return _poser(session, e, "filmeur", _ecran_choix(e, "film", "Qui filmait aujourd'hui ?", FILMEURS), compter)
        inconnue = next((p for p in pistes if p.valeur not in pistes_connues() and not p.verifie), None)
        if inconnue is not None:
            return _poser(session, e, "pistes", _ecran_piste(e, inconnue), compter)
        if not _faits(session, e, "moment") and not _faits(session, e, "moment_passe"):
            return _poser(session, e, "moments", Message(
                "Un moment qui t'a marqué aujourd'hui ? Réponds en vocal.",
                [[("Passer cette question", f"e|{e.id}|skipm")], _boutons_sortie(e)]), compter)
    return _poser(session, e, "chutes", _ecran_chutes(session, e, materialiser=True), compter)


def _ecran_presents(session: Session, e: Entretien, masque: int | None) -> Message:
    orga = _organisateur(session)
    autres = list(session.scalars(select(Personne).where(Personne.id != orga.id, Personne.actif.is_(True)).order_by(Personne.id)))
    if masque is None:
        presents = _ids_presents(session, e)
        masque = sum(1 << p.id for p in autres if p.id in presents)
    boutons = [[(("✅ " if masque & (1 << p.id) else "") + p.prenom, f"e|{e.id}|pres|{masque ^ (1 << p.id)}")] for p in autres]
    boutons.append([("Valider", f"e|{e.id}|presok|{masque}")])
    return Message("Qui était là aujourd'hui avec toi ? (coché = présent)", boutons)


def _ecran_stations(e: Entretien, masque: int) -> Message:
    boutons = [[(("✅ " if masque & (1 << i) else "") + s, f"e|{e.id}|stat|{masque ^ (1 << i)}")]
               for i, s in enumerate(config.STATIONS)]
    boutons.append([("Valider", f"e|{e.id}|statok|{masque}")])
    return Message("Où avez-vous skié ? (une ou plusieurs stations)", boutons)


def _ecran_choix(e: Entretien, action: str, question: str, options: list[str]) -> Message:
    return Message(question, [[(o, f"e|{e.id}|{action}|{i}")] for i, o in enumerate(options)])


def _ecran_piste(e: Entretien, champ: ChampJournal) -> Message:
    boutons = [[(p, f"e|{e.id}|piste|{champ.id}|{j}")] for j, p in enumerate(pistes_connues())]
    boutons.append([(f"Garder « {champ.valeur} »", f"e|{e.id}|piste|{champ.id}|k")])
    return Message(f"J'ai noté « {champ.valeur} ». C'est quelle piste ?", boutons)


def _chutes_comptees(session: Session, e: Entretien) -> dict[str, int]:
    lignes = session.execute(select(Personne.prenom, func.count()).select_from(Chute).join(Personne).where(
        Chute.journee_id == e.journee_id).group_by(Personne.prenom).order_by(Personne.id)).all()
    return {prenom: n for prenom, n in lignes}


def _ecran_chutes(session: Session, e: Entretien, materialiser: bool = False) -> Message:
    if materialiser:  # les chutes dites dans le vocal deviennent des chutes comptées, une seule fois
        for champ in _faits(session, e, "chute_vocal"):
            if not champ.verifie:
                personne = session.scalar(select(Personne).where(Personne.prenom == champ.valeur))
                for _ in range(int(champ.detail or 1)):
                    session.add(Chute(journee_id=e.journee_id, personne_id=personne.id))
                champ.verifie = True
        session.commit()
    comptees = _chutes_comptees(session, e)
    deja = ", ".join(f"{p} ×{n}" for p, n in comptees.items()) or "aucune"
    presents = session.scalars(select(Personne).where(Personne.id.in_([*_ids_presents(session, e)])).order_by(Personne.id))
    boutons = [[(f"+1 {p.prenom}", f"e|{e.id}|chute|{p.id}")] for p in presents]
    boutons.append([("Terminé", f"e|{e.id}|chutefin")])
    return Message(f"Des chutes aujourd'hui ? Déjà comptées : {deja}.\nTouche +1 pour chaque chute oubliée, "
                   "ou « Terminé » s'il n'y en a pas d'autre.", boutons)


# --- Boutons -----------------------------------------------------------------

def bouton(session: Session, data: str, personne_id: int) -> Reaction:
    """Traite un appui sur un bouton de l'entretien."""
    morceaux = data.split("|")
    if morceaux[1] == "go":
        return Reaction(suite=demarrer(session, int(morceaux[2]), personne_id))
    e = session.get(Entretien, int(morceaux[1]))
    if e is None or e.personne_id != personne_id:
        return Reaction(edition=Message("Cet entretien n'existe plus."))
    action, args = morceaux[2], morceaux[3:]

    if action == "passer":
        return Reaction(edition=passer(session, e))
    if action == "tard":
        return Reaction(edition=plus_tard(session, e))
    if action == "reprendre":
        e.etat = "en_cours"
        session.commit()
        return Reaction(edition=Message("▶️ On reprend."), suite=[question_suivante(session, e, compter=False)])
    if e.etat != "en_cours":
        return Reaction(edition=Message("Cet entretien n'est plus en cours."))

    if action == "pres":
        return Reaction(edition=_ecran_presents(session, e, int(args[0])))
    if action == "presok":
        masque = int(args[0])
        orga = _organisateur(session)
        session.execute(delete(Presence).where(Presence.journee_id == e.journee_id))
        ids = [orga.id] + [p.id for p in session.scalars(select(Personne).where(Personne.id != orga.id)) if masque & (1 << p.id)]
        session.add_all(Presence(journee_id=e.journee_id, personne_id=i) for i in ids)
        noms = ", ".join(p.prenom for p in session.scalars(select(Personne).where(Personne.id.in_(ids)).order_by(Personne.id)))
        _bouton(session, e, "presents_ok", noms)
        session.commit()
        return Reaction(edition=Message(f"Présents : {noms}."), suite=[question_suivante(session, e)])
    if action == "stat":
        return Reaction(edition=_ecran_stations(e, int(args[0])))
    if action == "statok":
        masque = int(args[0])
        if masque == 0:
            return Reaction(edition=_ecran_stations(e, 0))
        choisies = [s for i, s in enumerate(config.STATIONS) if masque & (1 << i)]
        for s in choisies:
            _bouton(session, e, "station", s)
        session.commit()
        return Reaction(edition=Message(f"Stations : {', '.join(choisies)}."), suite=[question_suivante(session, e)])
    if action in ("meteo", "neige", "ton"):
        options = {"meteo": config.METEOS, "neige": config.NEIGES, "ton": config.TONS}[action]
        choix = options[int(args[0])]
        _bouton(session, e, action, choix.split(" ", 1)[1])
        session.commit()
        return Reaction(edition=Message(f"{choix}"), suite=[question_suivante(session, e)])
    if action == "film":
        choix = FILMEURS[int(args[0])]
        _bouton(session, e, "filmeur", choix)
        session.commit()
        return Reaction(edition=Message(f"Filmeur : {choix}."), suite=[question_suivante(session, e)])
    if action == "piste":
        champ = session.get(ChampJournal, int(args[0]))
        if args[1] != "k":
            champ.valeur = pistes_connues()[int(args[1])]
        champ.verifie = True
        session.commit()
        return Reaction(edition=Message(f"Piste : {champ.valeur}."), suite=[question_suivante(session, e)])
    if action == "skipm":
        _bouton(session, e, "moment_passe", "passé")
        session.commit()
        return Reaction(edition=Message("OK, pas de moment marquant."), suite=[question_suivante(session, e)])
    if action == "chute":
        session.add(Chute(journee_id=e.journee_id, personne_id=int(args[0])))
        session.commit()
        return Reaction(edition=_ecran_chutes(session, e))
    if action == "chutefin":
        return Reaction(edition=Message("Chutes notées."), suite=[_finaliser(session, e)])
    return Reaction()


def _finaliser(session: Session, e: Entretien) -> Message:
    """Range les pistes descendues en descentes (avec qui filmait) et clôt l'entretien."""
    journee = _journee(session, e)
    stations = ", ".join(f.valeur for f in _faits(session, e, "station"))
    sequence = Sequence(journee_id=e.journee_id, ordre=1, lieu=stations or None)
    session.add(sequence)
    session.flush()
    filmeur_unique = next((f.valeur for f in _faits(session, e, "filmeur") if f.valeur in ("Maxime", "Adrien")), None)
    for piste in _faits(session, e, "piste"):
        prenom = piste.detail or filmeur_unique
        personne = session.scalar(select(Personne).where(Personne.prenom == prenom)) if prenom else None
        session.add(Descente(sequence_id=sequence.id, piste=piste.valeur, filmeur_id=personne.id if personne else None))
    ton = _faits(session, e, "ton")
    if ton:
        sequence.ton = ton[0].valeur
    e.etat, e.attente = "termine", "fini"
    session.commit()
    return Message(f"✅ Journal du {_jour_lisible(journee.jour)} enregistré. /journal pour le relire.")
