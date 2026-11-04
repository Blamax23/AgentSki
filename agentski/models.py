"""Schéma v1. Les heures sont stockées en UTC ; l'affichage et les rappels sont en Europe/Paris."""
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Personne(Base):
    __tablename__ = "personne"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(unique=True)  # None tant qu'il n'a pas envoyé /start
    prenom: Mapped[str] = mapped_column(String(50), unique=True)
    # duo_saison | ami | hote
    statut: Mapped[str] = mapped_column(String(20), default="ami")
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    premiere_apparition: Mapped[date | None] = mapped_column(Date)


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
    envoyer_a: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    texte: Mapped[str | None] = mapped_column(Text)
    envoye_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Entretien(Base):
    __tablename__ = "entretien"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    # en_cours | reporte | passe | termine
    etat: Mapped[str] = mapped_column(String(20), default="en_cours")
    relances: Mapped[int] = mapped_column(Integer, default=0)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Transcription(Base):
    __tablename__ = "transcription"

    id: Mapped[int] = mapped_column(primary_key=True)
    entretien_id: Mapped[int] = mapped_column(ForeignKey("entretien.id"))
    texte_brut: Mapped[str] = mapped_column(Text)
    duree_s: Mapped[float | None]
    cout: Mapped[float | None]
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChampJournal(Base):
    """Un fait du journal, avec la phrase de la transcription d'où il vient."""
    __tablename__ = "champ_journal"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    transcription_id: Mapped[int | None] = mapped_column(ForeignKey("transcription.id"))
    cle: Mapped[str] = mapped_column(String(50))  # station, piste, meteo, neige, note, moment...
    valeur: Mapped[str] = mapped_column(Text)
    phrase_source: Mapped[str | None] = mapped_column(Text)
    supprime: Mapped[bool] = mapped_column(Boolean, default=False)  # « supprime ça »


# Tables des fonctions futures déjà prévues : /chute, /moment, /citation.
class Chute(Base):
    __tablename__ = "chute"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Moment(Base):
    __tablename__ = "moment"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    texte: Mapped[str | None] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Citation(Base):
    __tablename__ = "citation"

    id: Mapped[int] = mapped_column(primary_key=True)
    journee_id: Mapped[int] = mapped_column(ForeignKey("journee.id"))
    personne_id: Mapped[int] = mapped_column(ForeignKey("personne.id"))
    texte: Mapped[str] = mapped_column(Text)
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
