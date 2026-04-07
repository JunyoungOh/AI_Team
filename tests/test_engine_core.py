"""Core engine tests — checkpoint, resume, interrupt, state merge.

Tests the self-built pipeline engine that replaces LangGraph.
These are critical infrastructure tests that must all pass.
"""

import asyncio
import json
import os
import tempfile

import pytest

from src.engine.checkpointer import SqliteCheckpointer, CheckpointSnapshot
from src.engine.interrupt import InterruptRequest, request_interrupt
from src.engine.state import merge_state, register_append_field
from src.engine.pipeline import PipelineEngine, CompiledPipeline, ResumeCommand


# ── State merge ─────────────────────────────────


def test_merge_state_overwrites_scalars():
    base = {"phase": "intake", "user_task": "hello"}
    update = {"phase": "routing"}
    result = merge_state(base, update)
    assert result["phase"] == "routing"
    assert result["user_task"] == "hello"


def test_merge_state_appends_messages():
    base = {"messages": [{"role": "user", "content": "hi"}]}
    update = {"messages": [{"role": "assistant", "content": "hey"}]}
    result = merge_state(base, update)
    assert len(result["messages"]) == 2


def test_merge_state_replaces_non_list():
    base = {"messages": "not a list"}
    update = {"messages": [{"role": "user", "content": "hi"}]}
    result = merge_state(base, update)
    assert result["messages"] == [{"role": "user", "content": "hi"}]


def test_merge_state_new_key():
    base = {"a": 1}
    update = {"b": 2}
    result = merge_state(base, update)
    assert result["a"] == 1
    assert result["b"] == 2


# ── Interrupt mechanism ─────────────────────────


def test_request_interrupt_raises_without_resume():
    state = {"phase": "test"}
    with pytest.raises(InterruptRequest) as exc_info:
        request_interrupt(state, {"type": "question", "data": "Q1?"})
    assert exc_info.value.payload["type"] == "question"


def test_request_interrupt_returns_resume_value():
    state = {"phase": "test", "_resume_value": "user answer"}
    result = request_interrupt(state, {"type": "question"})
    assert result == "user answer"


# ── Checkpointer ────────────────────────────────


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_checkpoint_save_load(db_path):
    config = {"configurable": {"thread_id": "test-001"}}
    async with SqliteCheckpointer(db_path) as cp:
        state = {"phase": "routing", "user_task": "hello", "_step": 3}
        await cp.save(state, config)
        snapshot = await cp.load(config)

    assert snapshot is not None
    assert snapshot.step == 3
    assert snapshot.state["phase"] == "routing"
    assert snapshot.state["user_task"] == "hello"
    assert snapshot.pending_interrupt is None
    assert snapshot.next is False


@pytest.mark.asyncio
async def test_checkpoint_save_with_interrupt(db_path):
    config = {"configurable": {"thread_id": "test-002"}}
    async with SqliteCheckpointer(db_path) as cp:
        state = {
            "phase": "awaiting",
            "_step": 5,
            "_interrupt": {"type": "question", "data": "Q1?"},
            "_resume_node": "await_user_answers",
        }
        await cp.save(state, config)
        snapshot = await cp.load(config)

    assert snapshot is not None
    assert snapshot.step == 5
    assert snapshot.next is True
    assert snapshot.pending_interrupt["type"] == "question"
    # _resume_node must be preserved in state (not stripped)
    assert snapshot.state["_resume_node"] == "await_user_answers"


@pytest.mark.asyncio
async def test_checkpoint_load_latest_step(db_path):
    """load() returns the highest step, not the one with interrupt."""
    config = {"configurable": {"thread_id": "test-003"}}
    async with SqliteCheckpointer(db_path) as cp:
        # Step 5: interrupt checkpoint
        state_interrupt = {
            "phase": "awaiting", "_step": 5,
            "_interrupt": {"type": "q"}, "_resume_node": "node_a",
        }
        await cp.save(state_interrupt, config)

        # Step 6: normal checkpoint (after resume)
        state_normal = {"phase": "routing", "_step": 6}
        await cp.save(state_normal, config)

        snapshot = await cp.load(config)

    assert snapshot.step == 6
    assert snapshot.next is False  # no pending interrupt


@pytest.mark.asyncio
async def test_checkpoint_pydantic_roundtrip(db_path):
    """Pydantic __pydantic__ marker is stripped on load (plain dict)."""
    config = {"configurable": {"thread_id": "test-004"}}
    async with SqliteCheckpointer(db_path) as cp:
        # Simulate a Pydantic model that was serialized
        state = {
            "phase": "test",
            "_step": 1,
            "some_model": {"__pydantic__": "TestModel", "field": "value"},
        }
        # Manually save (bypass encoder since we're testing decoder)
        await cp._conn.execute(
            "INSERT OR REPLACE INTO checkpoints (thread_id, step, state, pending_interrupt)"
            " VALUES (?, ?, ?, ?)",
            ("test-004", 1, json.dumps(state), None),
        )
        await cp._conn.commit()
        snapshot = await cp.load(config)

    # __pydantic__ marker should be removed, rest preserved
    assert "__pydantic__" not in snapshot.state["some_model"]
    assert snapshot.state["some_model"]["field"] == "value"


# ── Pipeline engine ─────────────────────────────


def _make_test_engine():
    """Build a minimal 3-node pipeline: start → middle → end."""
    engine = PipelineEngine()

    def node_start(state):
        return {"phase": "middle", "messages": [{"content": "started"}]}

    def node_middle(state):
        return {"phase": "done", "messages": [{"content": "processed"}]}

    engine.add_node("start", node_start)
    engine.add_node("middle", node_middle)
    engine.set_entry("start")
    engine.set_router("start", lambda s: "middle")
    engine.set_router("middle", lambda s: "__end__")
    return engine


@pytest.mark.asyncio
async def test_pipeline_runs_to_completion():
    engine = _make_test_engine()
    app = engine.compile()
    events = []
    async for event in app.astream({"phase": "init", "messages": []}):
        events.append(event)

    assert len(events) == 2
    assert "start" in events[0]
    assert "middle" in events[1]


@pytest.mark.asyncio
async def test_pipeline_interrupt_and_resume(db_path):
    """Test that interrupt pauses and resume continues correctly."""
    engine = PipelineEngine()

    def node_ask(state):
        # Uses request_interrupt which raises InterruptRequest if no _resume_value
        answer = request_interrupt(state, {"type": "question", "q": "Name?"})
        return {"user_answer": answer, "phase": "finish"}

    def node_finish(state):
        return {"phase": "complete", "result": f"Hello {state.get('user_answer', '?')}"}

    engine.add_node("ask", node_ask)
    engine.add_node("finish", node_finish)
    engine.set_entry("ask")
    engine.set_router("ask", lambda s: "finish")
    engine.set_router("finish", lambda s: "__end__")

    async with SqliteCheckpointer(db_path) as cp:
        app = engine.compile(checkpointer=cp)
        config = {"configurable": {"thread_id": "resume-test"}}

        # Phase 1: run until interrupt
        events = []
        async for event in app.astream({"phase": "init", "messages": [], "_step": 0}, config=config):
            events.append(event)

        assert len(events) == 1
        assert "__interrupt__" in events[0]

        # Phase 2: resume with answer
        events2 = []
        async for event in app.astream(ResumeCommand(value="World"), config=config):
            events2.append(event)

        # Should get: ask completion + finish completion
        assert len(events2) == 2
        assert "ask" in events2[0]
        assert "finish" in events2[1]
        assert events2[1]["finish"]["result"] == "Hello World"


@pytest.mark.asyncio
async def test_pipeline_missing_router_raises():
    engine = PipelineEngine()
    engine.add_node("orphan", lambda s: {"phase": "done"})
    engine.set_entry("orphan")
    # No router registered for "orphan"

    app = engine.compile()
    with pytest.raises(KeyError, match="No router registered"):
        async for _ in app.astream({"phase": "init", "messages": []}):
            pass


@pytest.mark.asyncio
async def test_pipeline_step_counter_increments(db_path):
    """Verify _step increments correctly across nodes and survives resume."""
    engine = PipelineEngine()

    def node_a(state):
        answer = request_interrupt(state, {"type": "q"})
        return {"phase": "b"}

    def node_b(state):
        return {"phase": "done"}

    engine.add_node("a", node_a)
    engine.add_node("b", node_b)
    engine.set_entry("a")
    engine.set_router("a", lambda s: "b")
    engine.set_router("b", lambda s: "__end__")

    async with SqliteCheckpointer(db_path) as cp:
        app = engine.compile(checkpointer=cp)
        config = {"configurable": {"thread_id": "step-test"}}

        # Run to interrupt
        async for _ in app.astream({"phase": "init", "messages": [], "_step": 0}, config=config):
            pass

        # Check interrupt checkpoint step
        snap1 = await cp.load(config)
        interrupt_step = snap1.step
        assert interrupt_step > 0

        # Resume
        async for _ in app.astream(ResumeCommand(value="ok"), config=config):
            pass

        # Final checkpoint should have higher step than interrupt
        snap2 = await cp.load(config)
        assert snap2.step > interrupt_step
        assert snap2.next is False  # no pending interrupt
