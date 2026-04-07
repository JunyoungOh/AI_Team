import pytest
from src.modes.common import get_mode_event_queue, emit_mode_event, cleanup_mode_event_queue

def test_emit_and_drain():
    sid = "test_session_001"
    q = get_mode_event_queue(sid)
    emit_mode_event(sid, {"type": "utterance", "data": {"speaker": "A", "content": "hello"}})
    emit_mode_event(sid, {"type": "utterance", "data": {"speaker": "B", "content": "hi"}})
    assert q.qsize() == 2
    e1 = q.get_nowait()
    assert e1["type"] == "utterance"
    assert e1["data"]["speaker"] == "A"
    e2 = q.get_nowait()
    assert e2["data"]["speaker"] == "B"
    assert q.empty()
    cleanup_mode_event_queue(sid)

def test_emit_without_queue_is_noop():
    emit_mode_event("nonexistent_session", {"type": "test", "data": {}})

def test_cleanup_removes_queue():
    sid = "cleanup_test"
    q = get_mode_event_queue(sid)
    emit_mode_event(sid, {"type": "test", "data": {}})
    cleanup_mode_event_queue(sid)
    q2 = get_mode_event_queue(sid)
    assert q2.empty()
