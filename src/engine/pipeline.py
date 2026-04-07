"""PipelineEngine — LangGraph StateGraph + compile() 대체.

Phase-driven while 루프로 노드를 순서대로 실행한다.
노드 함수는 기존 state: dict → dict 시그니처 그대로 사용.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

from src.engine.checkpointer import SqliteCheckpointer, CheckpointSnapshot
from src.engine.interrupt import InterruptRequest
from src.engine.state import merge_state

NodeFn = Callable[[dict], dict]
RouteFn = Callable[[dict], str]


@dataclass
class ResumeCommand:
    """LangGraph Command(resume=value) 대체."""
    value: Any


class PipelineEngine:
    def __init__(self) -> None:
        self._nodes: dict[str, NodeFn] = {}
        self._routers: dict[str, RouteFn] = {}
        self._entry: str = ""

    def add_node(self, name: str, fn: NodeFn) -> None:
        self._nodes[name] = fn

    def set_entry(self, name: str) -> None:
        self._entry = name

    def set_router(self, name: str, fn: RouteFn) -> None:
        self._routers[name] = fn

    def compile(self, checkpointer: SqliteCheckpointer | None = None) -> "CompiledPipeline":
        assert self._entry, "Entry node not set"
        return CompiledPipeline(
            nodes=dict(self._nodes),
            routers=dict(self._routers),
            entry=self._entry,
            checkpointer=checkpointer,
        )


class CompiledPipeline:
    def __init__(
        self,
        nodes: dict[str, NodeFn],
        routers: dict[str, RouteFn],
        entry: str,
        checkpointer: SqliteCheckpointer | None,
    ) -> None:
        self._nodes = nodes
        self._routers = routers
        self._entry = entry
        self._checkpointer = checkpointer

    async def astream(
        self,
        input_data: dict | ResumeCommand,
        *,
        config: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        config = config or {"configurable": {"thread_id": "default"}}

        if isinstance(input_data, ResumeCommand):
            async for event in self._resume(input_data.value, config=config):
                yield event
            return

        state = input_data
        async for event in self._run_from(self._entry, state, config):
            yield event

    async def _run_from(
        self,
        start_node: str,
        state: dict,
        config: dict,
    ) -> AsyncGenerator[dict, None]:
        current = start_node

        while current != "__end__":
            try:
                update = await _call_node(self._nodes[current], state, config)
            except InterruptRequest as req:
                state["_interrupt"] = req.payload
                state["_resume_node"] = current
                state["_step"] = state.get("_step", 0) + 1
                if self._checkpointer:
                    await self._checkpointer.save(state, config)
                yield {"__interrupt__": req.payload}
                return

            state = merge_state(state, update)
            state["_step"] = state.get("_step", 0) + 1
            if self._checkpointer:
                await self._checkpointer.save(state, config)
            yield {current: update}

            router = self._routers.get(current)
            if router is None:
                raise KeyError(
                    f"No router registered for node '{current}'. "
                    f"Call engine.set_router('{current}', ...) in graph definition."
                )
            current = router(state)

    async def _resume(
        self,
        resume_value: Any,
        *,
        config: dict,
    ) -> AsyncGenerator[dict, None]:
        if not self._checkpointer:
            raise ValueError("Cannot resume without checkpointer")
        snapshot = await self._checkpointer.load(config)
        if not snapshot:
            thread_id = config.get("configurable", {}).get("thread_id", "?")
            raise ValueError(f"No checkpoint found for thread_id={thread_id}")

        state = snapshot.state
        state["_step"] = snapshot.step  # step 카운터 복원 (DB 컬럼에서)
        state["_resume_value"] = resume_value
        resume_node = state.pop("_resume_node", self._entry)
        state.pop("_interrupt", None)

        try:
            update = await _call_node(self._nodes[resume_node], state, config)
        except InterruptRequest as req:
            # FIX: 재인터럽트 시에도 _step 증가 (체크포인트 덮어쓰기 방지)
            state.pop("_resume_value", None)
            state["_interrupt"] = req.payload
            state["_resume_node"] = resume_node
            state["_step"] = state.get("_step", 0) + 1
            if self._checkpointer:
                await self._checkpointer.save(state, config)
            yield {"__interrupt__": req.payload}
            return

        state = merge_state(state, update)
        state.pop("_resume_value", None)
        state["_step"] = state.get("_step", 0) + 1
        if self._checkpointer:
            await self._checkpointer.save(state, config)
        yield {resume_node: update}

        # FIX: 라우터 누락 시 KeyError 발생 (기존: 무조건 __end__ 폴백)
        router = self._routers.get(resume_node)
        if router is None:
            raise KeyError(
                f"No router registered for node '{resume_node}'. "
                f"Call engine.set_router('{resume_node}', ...) in graph definition."
            )
        next_node = router(state)
        async for event in self._run_from(next_node, state, config):
            yield event

    async def aget_state(self, config: dict) -> CheckpointSnapshot | None:
        if not self._checkpointer:
            return None
        return await self._checkpointer.load(config)


async def _call_node(fn: NodeFn, state: dict, config: dict) -> dict:
    """노드 함수 호출 — sync/async 자동 감지, config 인자 자동 감지.

    FIX: 파라미터 이름 'config'으로 감지 (기존: 필수 파라미터 개수 기반 → 오작동 가능)
    주의: @node_error_handler 데코레이터가 적용된 노드는 wrapper(state) 시그니처로
    감싸져 있어서 needs_config=False가 된다. 이것이 의도된 동작이다.

    Sync 노드는 내부적으로 run_async()를 사용하여 subprocess를 실행함.
    run_async는 ThreadPoolExecutor에서 asyncio.run(coro)을 실행하고
    future.result()로 blocking 대기하므로 event loop을 block함.
    이는 의도된 동작 — subprocess 자체가 별도 thread의 event loop에서
    실행되므로 timeout이 정상 작동함. main loop의 heartbeat는
    별도 asyncio.Task로 동작하여 blocking 영향을 받지 않음.
    """
    sig = inspect.signature(fn)
    needs_config = "config" in sig.parameters

    if inspect.iscoroutinefunction(fn):
        return await fn(state, config) if needs_config else await fn(state)
    return fn(state, config) if needs_config else fn(state)
