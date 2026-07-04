"""ORM models. One row per (report_date, side, symbol) per table."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class DeliveryPick(Base):
    """A scored delivery pick (BUY or SELL list) for one trading day."""
    __tablename__ = "delivery_picks"
    __table_args__ = (
        UniqueConstraint("report_date", "side", "symbol", name="uq_delivery_pick"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[dt.date] = mapped_column(Date, index=True)
    side: Mapped[str] = mapped_column(String(4))          # BUY | SELL
    rank: Mapped[int] = mapped_column(Integer)            # 1-based within the list
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cap: Mapped[str | None] = mapped_column(String(8), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    deliv_per: Mapped[float | None] = mapped_column(Float, nullable=True)
    deliv_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vol_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    inst_buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    inst_sell: Mapped[float | None] = mapped_column(Float, nullable=True)


class Favorite(Base):
    """User watchlist: symbols whose full delivery details are computed each run."""
    __tablename__ = "favorites"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    added_at: Mapped[dt.datetime] = mapped_column(
        default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class BreakoutWatch(Base):
    """User-curated breakout watchlist: symbols to always score for breakout
    stage regardless of whether they clear the auto-detection threshold."""
    __tablename__ = "breakout_watch"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    added_at: Mapped[dt.datetime] = mapped_column(
        default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class MarketFlow(Base):
    """Market-wide FII/DII cash buy/sell/net (Rs crore) for one trading day."""
    __tablename__ = "market_flows"
    __table_args__ = (
        UniqueConstraint("report_date", "category", name="uq_market_flow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[dt.date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(8))      # FII | DII
    buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell: Mapped[float | None] = mapped_column(Float, nullable=True)
    net: Mapped[float | None] = mapped_column(Float, nullable=True)
    flow_date: Mapped[str | None] = mapped_column(String(16), nullable=True)  # NSE-reported date


class BulkDeal(Base):
    """An aggregated bulk-deal row (BUY or SELL) for one trading day."""
    __tablename__ = "bulk_deals"
    __table_args__ = (
        UniqueConstraint("report_date", "side", "symbol", name="uq_bulk_deal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[dt.date] = mapped_column(Date, index=True)
    side: Mapped[str] = mapped_column(String(4))          # BUY | SELL
    rank: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    n_deals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_cr: Mapped[float | None] = mapped_column(Float, nullable=True)
    institutions: Mapped[str | None] = mapped_column(String, nullable=True)
