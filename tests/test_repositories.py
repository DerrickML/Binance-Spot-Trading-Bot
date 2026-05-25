"""Tests for persistence repository behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.persistence.db import get_session, init_db, reset_engine
from app.persistence.models import Candle, GridEvent, GridState
from app.persistence.repositories import CandleRepository, GridEventRepository, GridStateRepository


@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _candle(idx: int) -> Candle:
    open_time = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=idx)
    return Candle(
        symbol="BTCUSDT",
        interval="1h",
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=100 + idx,
        high=101 + idx,
        low=99 + idx,
        close=100 + idx,
        volume=1000,
    )


def test_get_candles_limit_defaults_to_latest_rows_in_chronological_order():
    session = get_session("sqlite:///:memory:")
    repo = CandleRepository(session)

    for idx in range(10):
        repo.upsert(_candle(idx))
    session.commit()

    rows = repo.get_candles("BTCUSDT", "1h", limit=3)
    session.close()

    assert [row.close for row in rows] == [107, 108, 109]


def test_get_candles_can_request_oldest_rows():
    session = get_session("sqlite:///:memory:")
    repo = CandleRepository(session)

    for idx in range(10):
        repo.upsert(_candle(idx))
    session.commit()

    rows = repo.get_candles("BTCUSDT", "1h", limit=3, latest=False)
    session.close()

    assert [row.close for row in rows] == [100, 101, 102]


def test_get_candles_excludes_unclosed_future_rows_by_default():
    session = get_session("sqlite:///:memory:")
    repo = CandleRepository(session)

    closed = _candle(0)
    future_open = datetime.now(timezone.utc) + timedelta(hours=1)
    unclosed = Candle(
        symbol="BTCUSDT",
        interval="1h",
        open_time=future_open,
        close_time=future_open + timedelta(hours=1),
        open=200,
        high=201,
        low=199,
        close=200,
        volume=1000,
    )
    repo.upsert(closed)
    repo.upsert(unclosed)
    session.commit()

    rows = repo.get_candles("BTCUSDT", "1h", limit=10)
    rows_with_unclosed = repo.get_candles(
        "BTCUSDT",
        "1h",
        limit=10,
        closed_only=False,
    )
    session.close()

    assert [row.close for row in rows] == [100]
    assert [row.close for row in rows_with_unclosed] == [100, 200]


def test_grid_state_upsert_and_event_save():
    session = get_session("sqlite:///:memory:")
    state_repo = GridStateRepository(session)
    event_repo = GridEventRepository(session)

    state_repo.upsert(GridState(
        mode="paper",
        strategy_name="hybrid_grid_dca",
        symbol="BTCUSDT",
        interval="1h",
        grid_id="grid-1",
        status="OPEN",
        anchor_price=100.0,
        average_entry_price=95.0,
        quantity=2.0,
        allocated_notional=190.0,
        allocation_pct=0.19,
        filled_levels_json="[0, 1]",
        params_json='{"max_grid_levels": 5}',
    ))
    event_repo.save(GridEvent(
        mode="paper",
        strategy_name="hybrid_grid_dca",
        symbol="BTCUSDT",
        interval="1h",
        grid_id="grid-1",
        event_type="scale_in",
        side="BUY",
        grid_level=1,
        price=95.0,
        quantity=1.0,
        notional=95.0,
        fees=0.095,
    ))
    session.commit()

    state_repo.upsert(GridState(
        mode="paper",
        strategy_name="hybrid_grid_dca",
        symbol="BTCUSDT",
        interval="1h",
        grid_id="grid-1",
        status="CLOSED",
        anchor_price=100.0,
        average_entry_price=95.0,
        quantity=2.0,
        allocated_notional=190.0,
        allocation_pct=0.19,
        filled_levels_json="[0, 1]",
        params_json='{"max_grid_levels": 5}',
        closed_at=datetime.now(timezone.utc),
    ))
    session.commit()

    state = session.query(GridState).filter_by(grid_id="grid-1").one()
    events = session.query(GridEvent).filter_by(grid_id="grid-1").all()
    session.close()

    assert state.status == "CLOSED"
    assert len(events) == 1
    assert events[0].event_type == "scale_in"
