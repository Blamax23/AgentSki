"""Schéma v1. Les heures sont stockées en UTC ; l'affichage et les rappels sont en Europe/Paris."""
from datetime import date, datetime, timezone

from sqlalchemy import false, true, Boolean, Date, DateTime, ForeignKey, Integer, MetaData, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    # Noms de contraintes explicites : SQLite en a besoin pour les modifier dans une migration
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s"})


class Personne(Base):
    __tablename__ = "personne"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(unique=True)  # None tant qu'il n'a pas envoyé /start
    prenom: Mapped[str] = mapped_column(String(50), unique=True)
    # duo_saison | ami | hote
    statut: Mapped[str] = mapped_column(String(20), default="ami")
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    premiere_apparition: Mapped[date | None] = mapped_column(Date)
    anniversaire: Mapped[date | None] = mapped_column(Date)  # l'année compte peu : seuls le jour et le mois servent
    surnom: Mapped[str | None] = mapped_column(String(50))  # donné par l'organisateur seul : Krok n'appelle plus la personne autrement


class Weekend(Base):
    __tablename__ = "weekend"

    id: Mapped[int] = mapped_column(primary_key=True)
    date_debut: Mapped[date] = mapped_column(Date)
    date_fin: Mapped[date] = mapped_column(Date)
    # a_confirmer | confirme | annule
    statut: Mapped[str] = mapped_column(String(20), default="a_confirmer")
    journees: Mapped[list["Journee"]] = relationship(back_populates="weekend")


class Journee(Base):
    __tablename__ = "journee"
    __table_args__ = (UniqueConstraint("jour"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    weekend_id: Mapped[int] = mapped_column(ForeignKey("weekend.id"))
    jour: Mapped[date] = mapped_column(Date)
    ski: Mapped[bool] = mapped_column(Boolean, default=True)
    weekend: Mapped[Weekend] = relationship(back_populates="journees")
    presences: Mapped[list["Presence"]] = relationship(back_populates="journee")
    sequences: Mapped[list["Sequence"]] = relationship(back_populates="journee")


class Presence(Base):
    __tablename__ = "presence"
    __table_args__ = (UniqueConstraint("journee_id", "personne_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    journee: Mapped[Journee] = relationship(back_populates="presences")


class Sequence(Base):
    """Une partie d'une journée : ski ou hors piste (soirée, Nouvel An...)."""
    __tablename__ = "sequence"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    ordre: Mapped[int] = mapped_column(Integer, default=1)
    lieu: Mapped[str | None] = mapped_column(String(100))  # station, secteur ou « chez Alix »
    ton: Mapped[str | None] = mapped_column(String(50))
    journee: Mapped[Journee] = relationship(back_populates="sequences")
    descentes: Mapped[list["Descente"]] = relationship(back_populates="sequence")


class Descente(Base):
    """Une descente, avec qui filmait (POV)."""
    __tablename__ = "descente"

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id"))
    piste: Mapped[str | None] = mapped_column(String(100))
    filmeur_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))
    sequence: Mapped[Sequence] = relationship(back_populates="descentes")


class Rappel(Base):
    """Table « rappels à envoyer », lue chaque minute."""
    __tablename__ = "rappel"

    id: Mapped[int] = mapped_column(primary_key=True)
    destinataire_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    type: Mapped[str] = mapped_column(String(30))  # jeudi | matin | soir
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))  # pour matin et soir
    envoyer_a: Mapped[datetime] = mapped_column(DateTime)  # UTC naïf
    texte: Mapped[str | None] = mapped_column(Text)
    envoye_a: Mapped[datetime | None] = mapped_column(DateTime)
    abandonne_a: Mapped[datetime | None] = mapped_column(DateTime)  # trop en retard, ou week-end modifié


class Entretien(Base):
    __tablename__ = "entretien"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    # en_cours | reporte | passe | termine
    etat: Mapped[str] = mapped_column(String(20), default="en_cours")
    # Ce que le bot attend : vocal | presents | stations | meteo | neige | ton | filmeur | pistes | moments | chutes | fini
    attente: Mapped[str] = mapped_column(String(20), default="vocal")
    relances: Mapped[int] = mapped_column(Integer, default=0)
    correction_id: Mapped[int | None] = mapped_column(Integer)  # transcription en cours de correction
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Transcription(Base):
    __tablename__ = "transcription"

    id: Mapped[int] = mapped_column(primary_key=True)
    entretien_id: Mapped[int] = mapped_column(ForeignKey("entretien.id"))
    texte_brut: Mapped[str] = mapped_column(Text)
    texte_corrige: Mapped[str | None] = mapped_column(Text)  # si Maxime a corrigé ce que le bot avait compris
    periode: Mapped[str | None] = mapped_column(String(12))  # matin | midi | aprem | soir | bilan
    duree_s: Mapped[float | None]
    cout: Mapped[float | None]
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChampJournal(Base):
    """Un fait du journal, avec la phrase de la transcription d'où il vient."""
    __tablename__ = "champ_journal"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    entretien_id: Mapped[int | None] = mapped_column(ForeignKey("entretien.id"))
    transcription_id: Mapped[int | None] = mapped_column(ForeignKey("transcription.id"))
    cle: Mapped[str] = mapped_column(String(50))  # station, piste, meteo, neige, ton, moment, filmeur, chute_vocal...
    valeur: Mapped[str] = mapped_column(Text)
    phrase_source: Mapped[str | None] = mapped_column(Text)
    periode: Mapped[str | None] = mapped_column(String(12))  # moment de la journée où le fait a été raconté
    detail: Mapped[str | None] = mapped_column(String(100))  # qui filmait une piste, nombre d'une chute...
    verifie: Mapped[bool] = mapped_column(Boolean, default=False)  # confirmé par bouton : jamais réécrit par une extraction
    supprime: Mapped[bool] = mapped_column(Boolean, default=False)  # « supprime ça »


# Tables des fonctions futures déjà prévues : /chute, /moment, /citation.
class Chute(Base):
    __tablename__ = "chute"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    verte: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # chute sur une piste verte, pour /verte
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Moment(Base):
    __tablename__ = "moment"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))
    texte: Mapped[str | None] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Citation(Base):
    __tablename__ = "citation"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    texte: Mapped[str] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reglage(Base):
    """Réglages que le bot apprend en route : par exemple l'identifiant du groupe Telegram."""
    __tablename__ = "reglage"

    cle: Mapped[str] = mapped_column(String(50), primary_key=True)
    valeur: Mapped[str] = mapped_column(Text)


class Note(Base):
    """Note sur 10 (peur ou confiance) et phrase du soir d'une personne pour une journée ; sert aux incrustations."""
    __tablename__ = "note"
    __table_args__ = (UniqueConstraint("journee_id", "personne_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    note: Mapped[int] = mapped_column(Integer)
    phrase: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(Integer)  # message du bot qui attend la phrase (réponse forcée)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Media(Base):
    """Photo ou vidéo de moins de 20 Mo reçue via Telegram : seule la référence est gardée, jamais le fichier."""
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))
    type: Mapped[str] = mapped_column(String(20))  # photo | video | animation | document
    file_id: Mapped[str] = mapped_column(String(200))
    taille: Mapped[int | None] = mapped_column(Integer)
    legende: Mapped[str | None] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Capsule(Base):
    """Capsule temporelle : prédiction, objectif et message à soi-même, révélés au dernier week-end de la saison."""
    __tablename__ = "capsule"

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"), unique=True)
    prediction: Mapped[str | None] = mapped_column(Text)
    objectif: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(Integer)  # question du bot en attente de réponse
    revelee_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Archive(Base):
    """Archive d'easter egg : un média de moins de 20 Mo, qui est visé, et une description libre."""
    __tablename__ = "archive"

    id: Mapped[int] = mapped_column(primary_key=True)
    auteur_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    cible_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))
    type: Mapped[str | None] = mapped_column(String(20))
    file_id: Mapped[str | None] = mapped_column(String(200))
    taille: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    etape: Mapped[str] = mapped_column(String(12), default="media")  # media | cible | description | ok
    message_id: Mapped[int | None] = mapped_column(Integer)  # question en attente de réponse
    nb_envois: Mapped[int] = mapped_column(Integer, default=0)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Replique(Base):
    """Ligne improvisée envoyée avec une archive : gardée pour ne jamais répéter la même sur la même archive."""
    __tablename__ = "replique"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_id: Mapped[int] = mapped_column(ForeignKey("archive.id"))
    texte: Mapped[str] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Idee(Base):
    """Boîte à idées de plans (soirées, activités), reprise une fois dans les rappels du matin."""
    __tablename__ = "idee"

    id: Mapped[int] = mapped_column(primary_key=True)
    auteur_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    texte: Mapped[str] = mapped_column(Text)
    rappelee: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Pari(Base):
    __tablename__ = "pari"

    id: Mapped[int] = mapped_column(primary_key=True)
    auteur_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    texte: Mapped[str] = mapped_column(Text)
    statut: Mapped[str] = mapped_column(String(10), default="ouvert", server_default="ouvert")  # ouvert | clos
    gagnant_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Plat(Base):
    """Un plat de la liste de courses du week-end, avec ses précisions (« raclette sans charcuterie »)."""
    __tablename__ = "plat"

    id: Mapped[int] = mapped_column(primary_key=True)
    auteur_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    texte: Mapped[str] = mapped_column(Text)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Element(Base):
    """Réserve des défis : une phrase, un gage, un plat, une question... Les auteurs restent anonymes."""
    __tablename__ = "element"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(12))
    auteur_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))  # None : inventé par le bot
    texte: Mapped[str] = mapped_column(Text)
    utilise: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Defi(Base):
    __tablename__ = "defi"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(12))
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))
    tire_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))
    contenu: Mapped[str] = mapped_column(Text)
    etat: Mapped[str] = mapped_column(String(10), default="en_cours", server_default="en_cours")
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DefiVote(Base):
    """Validation d'un défi : vote, note ou réponse d'une personne (une seule par défi)."""
    __tablename__ = "defi_vote"
    __table_args__ = (UniqueConstraint("defi_id", "personne_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    defi_id: Mapped[int] = mapped_column(ForeignKey("defi.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    valeur: Mapped[int] = mapped_column(Integer)


class Morceau(Base):
    """Morceau ajouté à la playlist du week-end : le blind test devine qui l'a proposé."""
    __tablename__ = "morceau"

    id: Mapped[int] = mapped_column(primary_key=True)
    weekend_id: Mapped[int | None] = mapped_column(ForeignKey("weekend.id"))
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    track_id: Mapped[str] = mapped_column(String(40))
    titre: Mapped[str] = mapped_column(String(200))
    artistes: Mapped[str] = mapped_column(String(200))
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Chrono(Base):
    """Chrono sur une piste repère, relevé à la main : un des repères que tout le monde peut produire."""
    __tablename__ = "chrono"

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))
    piste: Mapped[str] = mapped_column(String(100))
    secondes: Mapped[int] = mapped_column(Integer)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Episode(Base):
    """Épisode publié sur YouTube, pour /vues."""
    __tablename__ = "episode"

    id: Mapped[int] = mapped_column(primary_key=True)
    titre: Mapped[str] = mapped_column(String(200))
    video_id: Mapped[str] = mapped_column(String(20), unique=True)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Activite(Base):
    """Fichier d'activité (FIT ou GPX) envoyé par une personne : seuls des résumés sont gardés, jamais la trace complète."""
    __tablename__ = "activite"
    __table_args__ = (UniqueConstraint("personne_id", "empreinte"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))
    source: Mapped[str] = mapped_column(String(5))  # fit | gpx
    empreinte: Mapped[str] = mapped_column(String(60))  # début + durée + nombre de descentes : détecte un fichier reçu deux fois
    debut: Mapped[datetime] = mapped_column(DateTime)  # UTC naïf
    duree_s: Mapped[int] = mapped_column(Integer)
    nb_descentes: Mapped[int] = mapped_column(Integer)
    denivele_neg: Mapped[int] = mapped_column(Integer)  # mètres, somme des descentes
    vitesse_max: Mapped[float | None]  # km/h
    premiere_descente: Mapped[datetime | None] = mapped_column(DateTime)
    derniere_descente: Mapped[datetime | None] = mapped_column(DateTime)
    segments: Mapped[str] = mapped_column(Text, default="[]")  # JSON : début, milieu et fin de chaque descente (3 points), pas la trace
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Position(Base):
    """Position reçue pendant un partage en direct. Effacée après le week-end : elle ne sert qu'à répondre à « où ? »."""
    __tablename__ = "position"

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    journee_id: Mapped[int | None] = mapped_column(ForeignKey("journee.id"))
    lat: Mapped[float]
    lon: Mapped[float]
    precision_m: Mapped[float | None]
    recue_a: Mapped[datetime] = mapped_column(DateTime)  # UTC naïf


class Trophee(Base):
    """Un trophée (ou un niveau de trophée) débloqué par une personne, sans aucune saisie en plus."""
    __tablename__ = "trophee"
    __table_args__ = (UniqueConstraint("personne_id", "code", "niveau"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    code: Mapped[str] = mapped_column(String(30))
    niveau: Mapped[int] = mapped_column(Integer)  # 1 bronze, 2 argent, 3 or ; 1 pour un trophée sans niveaux
    debloque_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    annonce: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Devinette(Base):
    """Une question « Qui est-ce ? » : une citation sans son auteur, que le groupe doit attribuer."""
    __tablename__ = "devinette"

    id: Mapped[int] = mapped_column(primary_key=True)
    citation_id: Mapped[int] = mapped_column(ForeignKey("citation.id"))
    clos: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DevinetteReponse(Base):
    __tablename__ = "devinette_reponse"
    __table_args__ = (UniqueConstraint("devinette_id", "personne_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    devinette_id: Mapped[int] = mapped_column(ForeignKey("devinette.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    choix_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))


class Souvenir(Base):
    """Un message échangé avec Krok, gardé tel quel. `lieu` sépare le privé du groupe : le privé n'alimente jamais le groupe."""
    __tablename__ = "souvenir"

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    lieu: Mapped[str] = mapped_column(String(6))  # prive | groupe
    role: Mapped[str] = mapped_column(String(4))  # user | bot
    texte: Mapped[str] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime)  # UTC naïf


class Memoire(Base):
    """Ce que Krok a retenu d'une personne, résumé par un modèle, séparément pour le privé et pour le groupe."""
    __tablename__ = "memoire"
    __table_args__ = (UniqueConstraint("personne_id", "lieu"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    lieu: Mapped[str] = mapped_column(String(6))
    resume: Mapped[str] = mapped_column(Text, default="", server_default="")
    depuis_resume: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # messages échangés depuis le dernier résumé


class Confessionnal(Base):
    """Une ligne du plan de confessionnal d'une soirée : qui passe, avec quelle question. Le tournage se fait hors du bot,
    il ne garde que le plan et ce que l'organisateur dit en avoir suivi (`statut`)."""
    __tablename__ = "confessionnal"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    ordre: Mapped[int] = mapped_column(Integer)
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    autre_id: Mapped[int | None] = mapped_column(ForeignKey("personne.id"))  # le deuxième de la question, pour un duo
    type: Mapped[str] = mapped_column(String(12))  # libre | prediction | verdict | duo | special
    question: Mapped[str] = mapped_column(Text)
    statut: Mapped[str] = mapped_column(String(8), default="propose", server_default="propose")  # propose | fait | passe
    decide_a: Mapped[datetime | None] = mapped_column(DateTime)  # UTC naïf : l'heure du « fait », un repère pour les rushes
