from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from backend.config import settings


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    risk_amount: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    entry_reason: Mapped[str] = mapped_column(String(120), default="")
    exit_reason: Mapped[str] = mapped_column(String(120), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    screenshot_entry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    screenshot_exit: Mapped[str | None] = mapped_column(String(255), nullable=True)


class JournalEvent(Base):
    __tablename__ = "journal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(12), default="INFO")
    event: Mapped[str] = mapped_column(String(80), index=True)
    symbol: Mapped[str] = mapped_column(String(20), default="")
    strategy: Mapped[str] = mapped_column(String(40), default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class CandleRecord(Base):
    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="5m")
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)


class StrategyConfig(Base):
    __tablename__ = "strategy_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


if settings.database_url.startswith("sqlite"):
    Path("database").mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
    expire_on_commit=False,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
