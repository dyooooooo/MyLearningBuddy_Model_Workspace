from __future__ import annotations

import pytest

from app import transport_breaker


@pytest.fixture(autouse=True)
def _clean() -> None:
    transport_breaker.reset()
    yield
    transport_breaker.reset()


def test_trips_only_at_the_limit() -> None:
    aid = "agent-1"
    for _ in range(transport_breaker.LIMIT - 1):
        transport_breaker.note_failure(aid)
        assert not transport_breaker.is_tripped(aid)
    transport_breaker.note_failure(aid)
    assert transport_breaker.is_tripped(aid)


def test_a_good_turn_clears_the_record() -> None:
    aid = "agent-1"
    for _ in range(transport_breaker.LIMIT - 1):
        transport_breaker.note_failure(aid)
    transport_breaker.note_success(aid)
    transport_breaker.note_failure(aid)
    assert not transport_breaker.is_tripped(aid)


def test_strikes_do_not_leak_across_agents() -> None:
    for _ in range(transport_breaker.LIMIT):
        transport_breaker.note_failure("agent-1")
    assert transport_breaker.is_tripped("agent-1")
    assert not transport_breaker.is_tripped("agent-2")


def test_trip_retires_a_thread_on_the_first_report() -> None:
    transport_breaker.trip("agent-1")
    assert transport_breaker.is_tripped("agent-1")
    assert not transport_breaker.is_tripped("agent-2")


def test_trip_needs_an_agent_id() -> None:
    transport_breaker.trip("")
    assert not transport_breaker.is_tripped("")


def test_a_new_thread_has_no_agent_to_strike() -> None:
    assert transport_breaker.note_failure("") == 0
    assert not transport_breaker.is_tripped("")
    transport_breaker.note_success("")
