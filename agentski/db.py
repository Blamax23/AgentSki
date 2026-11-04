from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Personne

config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{config.DATABASE_PATH}")


@event.listens_for(engine, "connect")
def _pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def seed_personnes(session: Session) -> None:
    """Crée les personnes connues qui manquent (sans identifiant Telegram tant qu'elles n'ont pas fait /start)."""
    for prenom, statut in config.PERSONNES:
        p = session.scalar(select(Personne).where(Personne.prenom == prenom))
        if p is None:
            session.add(Personne(prenom=prenom, statut=statut))
        else:
            p.statut = statut
    session.commit()


def sync_allowed_users(session: Session) -> None:
    """Met la table personne en accord avec ALLOWED_USERS (.env)."""
    for tid, prenom in config.allowed_users().items():
        p = session.scalar(select(Personne).where(Personne.prenom == prenom))
        if p is None:
            session.add(Personne(prenom=prenom, telegram_id=tid))
        else:
            p.telegram_id = tid
    session.commit()
