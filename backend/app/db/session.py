"""Engine and session factory. The app keeps its sessionmaker on ``app.state`` (see ``app.main``)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_sessionmaker(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)
