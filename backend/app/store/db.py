from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.store.models import Deployment, DeployStatus

_engine: Optional[Engine] = None


def init_engine(database_url: str) -> Engine:
    global _engine
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: sessions are opened from worker threads (asyncio.to_thread).
    _engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(_engine)
    return _engine


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialised; call init_engine() first")
    return _engine


def session() -> Session:
    return Session(engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session() as s:
        yield s


def last_successful(s: Session, app_name: str, exclude_id: Optional[int] = None) -> Optional[Deployment]:
    q = (
        select(Deployment)
        .where(Deployment.app_name == app_name, Deployment.status == DeployStatus.SUCCEEDED)
        .order_by(Deployment.id.desc())
    )
    if exclude_id is not None:
        q = q.where(Deployment.id != exclude_id)
    return s.exec(q).first()
