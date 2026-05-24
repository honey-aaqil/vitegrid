from __future__ import annotations

import os
from datetime import datetime
from typing import Generator

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DB_URL = os.environ.get("VITEGRID_DB_URL", "sqlite:///./vitegrid.db")

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Template(Base):
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(16), nullable=False)
    original_file_path = Column(String(512), nullable=True)
    thumbnail_path = Column(String(512), nullable=True)
    layout_json = Column(Text, nullable=False)
    lock_tier = Column(Integer, nullable=False, default=3)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    images = relationship("ImageAsset", back_populates="template", cascade="all, delete-orphan")


class ImageAsset(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("templates.id", ondelete="CASCADE"), nullable=True)
    local_path = Column(String(512), nullable=False)
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(64), nullable=False)
    width_px = Column(Integer, nullable=False)
    height_px = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    template = relationship("Template", back_populates="images")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
