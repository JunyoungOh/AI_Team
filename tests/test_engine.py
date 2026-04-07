"""Unit tests for src/engine."""

import asyncio
import os
import tempfile

import pytest
from langchain_core.messages import AIMessage

from src.engine.checkpointer import SqliteCheckpointer, CheckpointSnapshot
from src.engine.state import merge_state
from src.engine.interrupt import InterruptRequest, request_interrupt


class TestMergeState:
    def test_last_writer_wins(self):
        base = {"phase": "intake", "user_task": "hello"}
        update = {"phase": "ceo_route"}
        result = merge_state(base, update)
        assert result["phase"] == "ceo_route"
        assert result["user_task"] == "hello"

    def test_messages_append(self):
        base = {"messages": [{"role": "user", "content": "a"}]}
        update = {"messages": [{"role": "ai", "content": "b"}]}
        result = merge_state(base, update)
        assert len(result["messages"]) == 2

    def test_utterances_append(self):
        base = {"utterances": [{"speaker": "A", "content": "hi"}]}
        update = {"utterances": [{"speaker": "B", "content": "hello"}]}
        result = merge_state(base, update)
        assert len(result["utterances"]) == 2

    def test_empty_update(self):
        base = {"phase": "intake", "messages": [1]}
        result = merge_state(base, {})
        assert result == base

    def test_new_field_in_update(self):
        base = {"phase": "intake"}
        update = {"error_message": "boom"}
        result = merge_state(base, update)
        assert result["error_message"] == "boom"


class TestInterrupt:
    def test_request_interrupt_raises_on_first_call(self):
        state = {"phase": "intake"}
        with pytest.raises(InterruptRequest) as exc_info:
            request_interrupt(state, {"type": "questions"})
        assert exc_info.value.payload == {"type": "questions"}

    def test_request_interrupt_returns_resume_value(self):
        state = {"phase": "intake", "_resume_value": {"answer": "yes"}}
        result = request_interrupt(state, {"type": "questions"})
        assert result == {"answer": "yes"}


class TestSqliteCheckpointer:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_save_and_load(self):
        async def _test():
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "test.db")
                config = {"configurable": {"thread_id": "t1"}}
                async with SqliteCheckpointer(db) as cp:
                    state = {"phase": "intake", "messages": [], "_step": 0}
                    await cp.save(state, config)
                    snapshot = await cp.load(config)
                    assert snapshot is not None
                    assert snapshot.state["phase"] == "intake"
                    assert snapshot.step == 0
        self._run(_test())

    def test_load_returns_none_for_unknown_thread(self):
        async def _test():
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "test.db")
                async with SqliteCheckpointer(db) as cp:
                    result = await cp.load({"configurable": {"thread_id": "unknown"}})
                    assert result is None
        self._run(_test())

    def test_interrupt_roundtrip(self):
        async def _test():
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "test.db")
                config = {"configurable": {"thread_id": "t2"}}
                async with SqliteCheckpointer(db) as cp:
                    state = {
                        "phase": "await", "_step": 1,
                        "_interrupt": {"type": "questions", "q": ["what?"]},
                    }
                    await cp.save(state, config)
                    snapshot = await cp.load(config)
                    assert snapshot.next is True
                    assert len(snapshot.tasks) == 1
                    assert snapshot.tasks[0].interrupts[0].value["type"] == "questions"
        self._run(_test())

    def test_message_serialization(self):
        async def _test():
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "test.db")
                config = {"configurable": {"thread_id": "t3"}}
                async with SqliteCheckpointer(db) as cp:
                    msg = AIMessage(content="hello from AI")
                    state = {"messages": [msg], "_step": 0}
                    await cp.save(state, config)
                    snapshot = await cp.load(config)
                    loaded_msg = snapshot.state["messages"][0]
                    assert isinstance(loaded_msg, AIMessage)
                    assert loaded_msg.content == "hello from AI"
        self._run(_test())

    def test_cleanup(self):
        async def _test():
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "test.db")
                config = {"configurable": {"thread_id": "t4"}}
                async with SqliteCheckpointer(db) as cp:
                    for i in range(10):
                        await cp.save({"_step": i, "phase": f"step_{i}"}, config)
                    await cp.cleanup("t4", keep_last=3)
                    cursor = await cp._conn.execute(
                        "SELECT COUNT(*) FROM checkpoints WHERE thread_id = 't4'"
                    )
                    count = (await cursor.fetchone())[0]
                    assert count == 3
        self._run(_test())

    def test_snapshot_values_compat(self):
        snapshot = CheckpointSnapshot(
            state={"phase": "done"}, pending_interrupt=None, step=5
        )
        assert snapshot.values == {"phase": "done"}
        assert snapshot.next is False
        assert snapshot.tasks == []


from src.engine.pipeline import PipelineEngine, ResumeCommand
from src.engine.interrupt import request_interrupt


class TestPipelineEngine:
    def test_simple_linear_pipeline(self):
        def node_a(state):
            return {"phase": "b", "messages": [{"content": "a"}]}
        def node_b(state):
            return {"phase": "c", "messages": [{"content": "b"}]}
        def node_c(state):
            return {"phase": "complete"}

        engine = PipelineEngine()
        engine.add_node("a", node_a)
        engine.add_node("b", node_b)
        engine.add_node("c", node_c)
        engine.set_entry("a")
        engine.set_router("a", lambda s: "b")
        engine.set_router("b", lambda s: "c")
        engine.set_router("c", lambda s: "__end__")

        async def _test():
            pipeline = engine.compile()
            events = []
            async for event in pipeline.astream({"messages": [], "phase": "start"}):
                events.append(event)
            assert len(events) == 3
            assert "a" in events[0]
            assert "c" in events[2]
        asyncio.run(_test())

    def test_conditional_routing(self):
        def node_start(state):
            return {"phase": "route"}
        def node_left(state):
            return {"result": "left"}
        def node_right(state):
            return {"result": "right"}
        def router(state):
            return "left" if state.get("go_left") else "right"

        engine = PipelineEngine()
        engine.add_node("start", node_start)
        engine.add_node("left", node_left)
        engine.add_node("right", node_right)
        engine.set_entry("start")
        engine.set_router("start", router)
        engine.set_router("left", lambda s: "__end__")
        engine.set_router("right", lambda s: "__end__")

        async def _test():
            pipeline = engine.compile()
            events = []
            async for e in pipeline.astream({"go_left": True}):
                events.append(e)
            assert "left" in events[1]
        asyncio.run(_test())

    def test_interrupt_and_resume(self):
        def ask_node(state):
            answer = request_interrupt(state, {"type": "question"})
            return {"user_answer": answer, "phase": "done"}
        def done_node(state):
            return {"result": f"got: {state['user_answer']}"}

        engine = PipelineEngine()
        engine.add_node("ask", ask_node)
        engine.add_node("done", done_node)
        engine.set_entry("ask")
        engine.set_router("ask", lambda s: "done")
        engine.set_router("done", lambda s: "__end__")

        async def _test():
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "test.db")
                config = {"configurable": {"thread_id": "t_interrupt"}}
                async with SqliteCheckpointer(db) as cp:
                    pipeline = engine.compile(checkpointer=cp)
                    events = []
                    async for e in pipeline.astream({"phase": "start"}, config=config):
                        events.append(e)
                    assert "__interrupt__" in events[0]

                    events2 = []
                    async for e in pipeline.astream(
                        ResumeCommand(value="my answer"), config=config
                    ):
                        events2.append(e)
                    assert "ask" in events2[0]
                    assert events2[0]["ask"]["user_answer"] == "my answer"
                    assert "done" in events2[1]
        asyncio.run(_test())

    def test_async_node(self):
        async def async_node(state):
            await asyncio.sleep(0.01)
            return {"phase": "done"}

        engine = PipelineEngine()
        engine.add_node("a", async_node)
        engine.set_entry("a")
        engine.set_router("a", lambda s: "__end__")

        async def _test():
            pipeline = engine.compile()
            events = []
            async for e in pipeline.astream({}):
                events.append(e)
            assert events[0]["a"]["phase"] == "done"
        asyncio.run(_test())

    def test_config_passed_to_two_arg_node(self):
        async def node_with_config(state, config):
            tid = config["configurable"]["thread_id"]
            return {"thread": tid}

        engine = PipelineEngine()
        engine.add_node("a", node_with_config)
        engine.set_entry("a")
        engine.set_router("a", lambda s: "__end__")

        async def _test():
            pipeline = engine.compile()
            config = {"configurable": {"thread_id": "test123"}}
            events = []
            async for e in pipeline.astream({}, config=config):
                events.append(e)
            assert events[0]["a"]["thread"] == "test123"
        asyncio.run(_test())

    def test_loop_routing(self):
        call_count = {"n": 0}
        def loop_node(state):
            call_count["n"] += 1
            return {"count": call_count["n"]}
        def router(state):
            return "loop" if state["count"] < 3 else "__end__"

        engine = PipelineEngine()
        engine.add_node("loop", loop_node)
        engine.set_entry("loop")
        engine.set_router("loop", router)

        async def _test():
            pipeline = engine.compile()
            events = []
            async for e in pipeline.astream({"count": 0}):
                events.append(e)
            assert len(events) == 3
            assert events[2]["loop"]["count"] == 3
        asyncio.run(_test())
