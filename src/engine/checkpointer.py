"""SQLite checkpointer — LangGraph AsyncSqliteSaver 대체."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from langchain_core.load import dumpd, load as lc_load
from langchain_core.messages import BaseMessage


class _StateEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, BaseMessage):
            return {"__lc_msg__": True, **dumpd(obj)}
        # Pydantic 모델 (DiscussionConfig 등)
        if hasattr(obj, "model_dump"):
            return {"__pydantic__": type(obj).__qualname__, **obj.model_dump()}
        return super().default(obj)


def _state_decoder_hook(d: dict) -> Any:
    if d.get("__lc_msg__"):
        d2 = {k: v for k, v in d.items() if k != "__lc_msg__"}
        return lc_load(d2)
    # FIX: Pydantic 모델은 dict로 남김 (resume 후에도 안전하게 .get() 접근 가능)
    # __pydantic__ 마커가 있으면 마커만 제거하고 plain dict로 반환
    if "__pydantic__" in d:
        return {k: v for k, v in d.items() if k != "__pydantic__"}
    return d


@dataclass
class InterruptInfo:
    value: Any

@dataclass
class TaskInfo:
    interrupts: list[InterruptInfo]

@dataclass
class CheckpointSnapshot:
    state: dict
    pending_interrupt: dict | None
    step: int

    @property
    def next(self) -> bool:
        return self.pending_interrupt is not None

    @property
    def tasks(self) -> list[TaskInfo]:
        if self.pending_interrupt:
            return [TaskInfo(interrupts=[InterruptInfo(value=self.pending_interrupt)])]
        return []

    @property
    def values(self) -> dict:
        return self.state


class SqliteCheckpointer:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "SqliteCheckpointer":
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # 구버전(LangGraph 시절) 테이블이 있으면 스키마 불일치 — 드롭 후 재생성
        cursor = await self._conn.execute("PRAGMA table_info(checkpoints)")
        columns = {row[1] for row in await cursor.fetchall()}
        if columns and "step" not in columns:
            await self._conn.execute("DROP TABLE checkpoints")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT NOT NULL,
                step      INTEGER NOT NULL,
                state     TEXT NOT NULL,
                pending_interrupt TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (thread_id, step)
            )
        """)
        await self._conn.commit()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def save(self, state: dict, config: dict) -> None:
        assert self._conn, "Checkpointer not opened"
        thread_id = config["configurable"]["thread_id"]
        step = state.get("_step", 0)
        interrupt = state.get("_interrupt")
        # 엔진 내부 키를 상태에서 제거 (DB에는 별도 컬럼 또는 불필요)
        # NOTE: _resume_node는 반드시 state에 보존해야 함 — resume 시 중단점 복원에 필요
        _INTERNAL_KEYS = {"_interrupt", "_step", "_resume_value"}
        state_clean = {k: v for k, v in state.items() if k not in _INTERNAL_KEYS}

        await self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints (thread_id, step, state, pending_interrupt)"
            " VALUES (?, ?, ?, ?)",
            (
                thread_id,
                step,
                json.dumps(state_clean, cls=_StateEncoder, ensure_ascii=False),
                json.dumps(interrupt, ensure_ascii=False) if interrupt else None,
            ),
        )
        await self._conn.commit()

    async def load(self, config: dict) -> CheckpointSnapshot | None:
        assert self._conn, "Checkpointer not opened"
        thread_id = config["configurable"]["thread_id"]
        cursor = await self._conn.execute(
            "SELECT state, pending_interrupt, step FROM checkpoints "
            "WHERE thread_id = ? ORDER BY step DESC LIMIT 1",
            (thread_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        state = json.loads(row[0], object_hook=_state_decoder_hook)
        interrupt = json.loads(row[1], object_hook=_state_decoder_hook) if row[1] else None
        return CheckpointSnapshot(state=state, pending_interrupt=interrupt, step=row[2])

    async def cleanup(self, thread_id: str, keep_last: int = 5) -> None:
        assert self._conn, "Checkpointer not opened"
        await self._conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ? AND step NOT IN "
            "(SELECT step FROM checkpoints WHERE thread_id = ? ORDER BY step DESC LIMIT ?)",
            (thread_id, thread_id, keep_last),
        )
        await self._conn.commit()
