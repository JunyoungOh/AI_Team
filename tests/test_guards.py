"""Unit tests for guard utilities: loop limits, error handler, safe conversions."""

import pytest
from langchain_core.messages import AIMessage
from src.engine.interrupt import InterruptRequest

from src.utils.guards import (
    LoopLimitExceeded,
    check_loop_limit,
    increment_loop_counter,
    node_error_handler,
    safe_int,
    safe_json_loads,
)


# ── Loop guards ──────────────────────────────────


def test_check_loop_limit_under():
    state = {"iteration_counts": {"retries": 1}}
    result = check_loop_limit(state, "retries", 3)
    assert result == 1


def test_check_loop_limit_at_limit_raises():
    state = {"iteration_counts": {"retries": 3}}
    with pytest.raises(LoopLimitExceeded) as exc_info:
        check_loop_limit(state, "retries", 3)
    assert exc_info.value.loop_name == "retries"
    assert exc_info.value.current == 3
    assert exc_info.value.limit == 3


def test_check_loop_limit_missing_key():
    state = {"iteration_counts": {}}
    result = check_loop_limit(state, "retries", 5)
    assert result == 0


def test_check_loop_limit_no_iteration_counts():
    state = {}
    result = check_loop_limit(state, "retries", 5)
    assert result == 0


def test_increment_loop_counter_new():
    state = {}
    counts = increment_loop_counter(state, "retries")
    assert counts["retries"] == 1


def test_increment_loop_counter_existing():
    state = {"iteration_counts": {"retries": 2, "other": 1}}
    counts = increment_loop_counter(state, "retries")
    assert counts["retries"] == 3
    assert counts["other"] == 1


def test_increment_does_not_mutate_original():
    original = {"iteration_counts": {"retries": 1}}
    counts = increment_loop_counter(original, "retries")
    assert counts["retries"] == 2
    assert original["iteration_counts"]["retries"] == 1


# ── node_error_handler ───────────────────────────


def test_node_error_handler_normal_execution():
    @node_error_handler("test_node")
    def my_node(state):
        return {"phase": "processing", "messages": []}

    result = my_node({"phase": "intake"})
    assert result["phase"] == "processing"


def test_node_error_handler_catches_exception():
    @node_error_handler("test_node")
    def bad_node(state):
        raise ValueError("Something broke")

    result = bad_node({"phase": "intake"})
    assert result["phase"] == "error"
    assert "test_node" in result["error_message"]
    assert "ValueError" in result["error_message"]
    assert "Something broke" in result["error_message"]
    assert len(result["messages"]) == 1


def test_node_error_handler_passes_through_error_state():
    @node_error_handler("test_node")
    def my_node(state):
        return {"phase": "processing"}

    result = my_node({"phase": "error"})
    assert result == {}


def test_node_error_handler_re_raises_interrupt_request():
    @node_error_handler("test_node")
    def interrupting_node(state):
        raise InterruptRequest({"type": "test", "msg": "pause here"})

    with pytest.raises(InterruptRequest):
        interrupting_node({"phase": "intake"})


# ── safe_int ─────────────────────────────────────


def test_safe_int_with_int():
    assert safe_int(42) == 42


def test_safe_int_with_str():
    assert safe_int("7") == 7


def test_safe_int_with_invalid():
    assert safe_int("abc") == 0


def test_safe_int_with_none():
    assert safe_int(None, default=5) == 5


def test_safe_int_with_float_str():
    assert safe_int("3.14") == 0


# ── safe_json_loads ──────────────────────────────


def test_safe_json_loads_valid():
    assert safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_loads_invalid():
    assert safe_json_loads("not json") is None


def test_safe_json_loads_empty():
    assert safe_json_loads("") is None


def test_safe_json_loads_none():
    assert safe_json_loads(None) is None


def test_safe_json_loads_default():
    assert safe_json_loads("bad", default=[]) == []
