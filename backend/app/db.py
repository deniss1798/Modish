from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config as _config  # noqa: F401 — загружает backend/.env
from .config import get_database_url

DATABASE_URL = get_database_url()

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()
