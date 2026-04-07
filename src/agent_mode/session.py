# src/agent_mode/session.py
"""WebSocket session handler for AI Agent mode."""
from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path

from src.agent_mode.mention_parser import MentionParser
from src.agent_mode.agent_router import AgentRouter
from src.agent_mode.agent_chat_manager import AgentChatManager
from src.agent_mode.bg_task_manager import BackgroundTaskManager
from src.config.agent_registry import LEADER_DOMAINS
from src.config.personas import get_worker_persona, WORKER_NAMES
from src.tools import get_claude_tools_for_domain
from src.config.personas import get_worker_name

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parents[2] / "data" / "agent_mode"

_RECOMMENDED_TASKS: dict[str, list[str]] = {
    "backend_developer": ["REST API 설계", "DB 스키마 모델링", "인증/인가 구현"],
    "frontend_developer": ["UI 컴포넌트 설계", "반응형 레이아웃", "상태 관리 전략"],
    "devops_engineer": ["CI/CD 파이프라인 구축", "Docker 컨테이너화", "모니터링 설정"],
    "architect": ["시스템 아키텍처 설계", "기술 스택 선정", "확장성 전략"],
    "tech_researcher": ["기술 트렌드 조사", "라이브러리 벤치마크", "PoC 평가"],
    "researcher": ["시장 조사", "경쟁사 분석", "문헌 리뷰"],
    "deep_researcher": ["심층 논문 분석", "학술 자료 수집", "연구 설계"],
    "data_analyst": ["데이터 분석", "대시보드 설계", "인사이트 도출"],
    "fact_checker": ["정보 검증", "출처 확인", "팩트체크 보고서"],
    "content_writer": ["블로그 작성", "마케팅 카피", "기술 문서"],
    "strategist": ["마케팅 전략 수립", "브랜드 포지셔닝", "캠페인 기획"],
    "designer": ["비주얼 디자인 가이드", "프레젠테이션 디자인", "브랜드 에셋"],
    "market_researcher": ["시장 규모 분석", "소비자 트렌드", "경쟁 환경 조사"],
    "project_manager": ["프로젝트 계획", "리스크 관리", "일정 조율"],
    "process_analyst": ["업무 프로세스 분석", "효율화 방안", "SOP 작성"],
    "ops_researcher": ["운영 효율 조사", "벤치마킹", "비용 최적화"],
    "financial_analyst": ["재무 분석", "투자 수익률 계산", "예산 시나리오"],
    "accountant": ["장부 관리", "세금 계산", "재무제표 작성"],
    "finance_researcher": ["금융 시장 조사", "규제 동향", "투자 리서치"],
    "recruiter": ["채용 공고 작성", "인재 풀 탐색", "면접 질문 설계"],
    "training_specialist": ["교육 프로그램 설계", "온보딩 가이드", "역량 평가"],
    "org_developer": ["조직 문화 진단", "팀 구조 설계", "변화 관리"],
    "compensation_analyst": ["보상 체계 분석", "시장 급여 조사", "인센티브 설계"],
    "hr_researcher": ["인사 트렌드 조사", "직원 만족도 분석", "복지 벤치마크"],
    "legal_counsel": ["계약서 검토", "법적 리스크 분석", "규정 준수 자문"],
    "compliance_officer": ["컴플라이언스 감사", "정책 수립", "규정 해석"],
    "ip_specialist": ["특허 분석", "지적재산 전략", "라이선스 검토"],
    "legal_researcher": ["판례 조사", "법률 동향 분석", "비교법 연구"],
    "data_engineer": ["ETL 파이프라인 설계", "데이터 웨어하우스 구축", "스키마 설계"],
    "ml_engineer": ["ML 모델 설계", "학습 파이프라인", "모델 배포"],
    "data_scientist": ["통계 분석", "예측 모델링", "A/B 테스트 설계"],
    "data_researcher": ["데이터셋 조사", "벤치마크 비교", "최신 논문 리뷰"],
    "product_manager": ["제품 로드맵 작성", "사용자 스토리 정의", "PRD 작성"],
    "ux_researcher": ["사용성 테스트 설계", "UX 감사", "사용자 인터뷰 가이드"],
    "product_analyst": ["제품 지표 분석", "퍼널 분석", "사용자 행동 분석"],
    "security_analyst": ["취약점 분석", "보안 감사", "위협 모델링"],
    "security_engineer": ["보안 인프라 설계", "WAF 설정", "암호화 구현"],
    "privacy_specialist": ["개인정보 영향 평가", "GDPR 준수 점검", "프라이버시 정책"],
    "security_researcher": ["보안 위협 조사", "CVE 분석", "공격 기법 연구"],
}


def _get_recommended_tasks(worker_id: str) -> list[str]:
    return _RECOMMENDED_TASKS.get(worker_id, [])


class AgentSession:
    def __init__(self, ws, user_id: str = "") -> None:
        self.ws = ws
        self._user_id = user_id
        self._session_id = f"agent_{uuid.uuid4().hex[:12]}"
        self._cancelled = False

        self._mention_parser = MentionParser()
        self._router = AgentRouter()
        self._chat_manager = AgentChatManager(
            session_id=self._session_id,
            summary_base_dir=_DATA_DIR / "summaries",
        )
        self._bg_manager = BackgroundTaskManager()
        self._engines: dict = {}
        self._selected_agent_ids: set[str] = set()  # 세션에 선택된 워커만

    async def run(self) -> None:
        self._startup_cleanup()
        await self._send(self._build_init_message())
        await self._chat_loop()

    async def _chat_loop(self) -> None:
        while not self._cancelled:
            try:
                msg = await self.ws.receive_json()
            except Exception:
                break

            msg_type = msg.get("type")
            if msg_type == "agent_stop":
                break
            elif msg_type == "agent_message":
                await self._handle_message(msg.get("content", ""))
            elif msg_type == "agent_select_workers":
                self._selected_agent_ids = set(msg.get("agent_ids", []))
            elif msg_type == "agent_bg_switch":
                await self._handle_bg_switch(msg.get("agent_id"))
            elif msg_type == "agent_bg_cancel":
                self._bg_manager.cancel_task(msg.get("task_id", ""))

        self._chat_manager.on_disconnect()

    async def _handle_message(self, content: str) -> None:
        parsed = self._mention_parser.parse(content)

        if parsed.agent_id:
            agent_id = parsed.agent_id
            message = parsed.message
            # 선택된 에이전트 범위 검증
            if self._selected_agent_ids and agent_id not in self._selected_agent_ids:
                await self._send({
                    "type": "agent_select_prompt",
                    "message": f"{get_worker_name(agent_id)}은(는) 현재 세션에 없습니다. 워커 편집으로 추가하세요.",
                })
                return
        else:
            message = parsed.message
            # 세션에 에이전트가 1명이면 무조건 그 에이전트에게 전달
            if len(self._selected_agent_ids) == 1:
                agent_id = next(iter(self._selected_agent_ids))
            else:
                # 다수 에이전트: 직전 대화 연속 체크
                agent_id = self._router.check_continuity(
                    message, self._chat_manager.active_agent_id
                )
                if not agent_id:
                    decision = await self._router.route(
                        message, self._chat_manager.active_agent_id
                    )
                    if decision and decision.confidence >= 0.6:
                        # 선택된 범위 내인지 확인
                        if not self._selected_agent_ids or decision.agent_id in self._selected_agent_ids:
                            agent_id = decision.agent_id
                            await self._send({
                                "type": "agent_routed",
                                "agent_id": agent_id,
                                "agent_name": get_worker_name(agent_id),
                                "reason": decision.reason,
                            })
                    if not agent_id:
                        # 선택된 에이전트 중 마지막 활성 에이전트에게 전달
                        agent_id = self._chat_manager.active_agent_id
                        if not agent_id and self._selected_agent_ids:
                            agent_id = next(iter(self._selected_agent_ids))

        self._chat_manager.set_active_agent(agent_id)
        self._chat_manager.add_message(agent_id, "user", message)

        display = self._chat_manager.get_agent_display(agent_id)
        system_prompt_template = self._chat_manager.build_system_prompt(agent_id)

        await self._stream_response(agent_id, message, system_prompt_template, display)

    def _get_or_create_engine(self, agent_id: str, system_prompt_template: str):
        if agent_id not in self._engines:
            from src.secretary.config import SecretaryConfig
            from src.secretary.chat_engine import ChatEngine
            from src.secretary.history_store import HistoryStore

            history_store = HistoryStore(
                session_id=self._session_id,
                user_id=self._user_id,
                base_dir=_DATA_DIR,
                prefix="agnt_",
            )
            config = SecretaryConfig()
            engine = ChatEngine(
                config=config,
                session_tag=f"agent_{self._session_id}_{agent_id}",
                session_id=self._session_id,
                user_id=self._user_id,
                system_prompt_template=system_prompt_template,
                history_store=history_store,
            )
            self._engines[agent_id] = engine
        return self._engines[agent_id]

    async def _stream_response(
        self,
        agent_id: str,
        message: str,
        system_prompt_template: str,
        display: dict,
    ) -> None:
        try:
            engine = self._get_or_create_engine(agent_id, system_prompt_template)

            original_send = self.ws.send_json

            async def tagged_send(data):
                if isinstance(data, dict) and data.get("type") == "sec_stream":
                    inner = data.get("data", {})
                    data = {
                        "type": "agent_stream",
                        "agent_id": display["agent_id"],
                        "agent_name": display["agent_name"],
                        "token": inner.get("token", ""),
                        "done": inner.get("done", False),
                    }
                    if "message_id" in inner:
                        data["message_id"] = inner["message_id"]
                await original_send(data)

            self.ws.send_json = tagged_send
            response = await engine.stream_response(message, self.ws)
            self.ws.send_json = original_send

            if response:
                self._chat_manager.add_message(agent_id, "assistant", response)

        except Exception as e:
            logger.error("Stream error for %s: %s", agent_id, e)
            await self._send({
                "type": "agent_stream",
                "agent_id": display["agent_id"],
                "agent_name": display["agent_name"],
                "token": f"오류가 발생했습니다: {e}",
                "done": True,
            })

    async def _handle_bg_switch(self, agent_id: str | None) -> None:
        if not agent_id:
            return
        task_id = self._bg_manager.start_task(agent_id, "background task", self.ws)
        if task_id:
            await self._send({
                "type": "agent_bg_started",
                "task_id": task_id,
                "agent_id": agent_id,
                "agent_name": get_worker_name(agent_id),
            })
        else:
            await self._send({
                "type": "error",
                "data": {"message": "백그라운드 작업이 최대치(3개)에 도달했습니다."},
            })

    def _build_init_message(self) -> dict:
        agents = []
        for domain, info in LEADER_DOMAINS.items():
            for wid in info["worker_types"]:
                persona = get_worker_persona(wid)
                meta = WORKER_NAMES.get(wid, {})
                tools = get_claude_tools_for_domain(wid)
                # Clean tool names for display
                tool_display = [
                    t.replace("mcp__", "").replace("__", " → ").replace("_", " ")
                    for t in tools[:6]  # show top 6
                ]
                agents.append({
                    "id": wid,
                    "name": get_worker_name(wid),
                    "domain": domain,
                    "position": wid.replace("_", " ").title(),
                    "role": persona.get("role") or wid.replace("_", " ").title(),
                    "expertise": persona.get("expertise", ""),
                    "keywords": meta.get("keywords", []),
                    "tools": tool_display,
                    "recommended_tasks": _get_recommended_tasks(wid),
                })
        return {
            "type": "agent_init",
            "session_id": self._session_id,
            "restored": False,
            "agents": agents,
        }

    def _startup_cleanup(self) -> None:
        sessions_dir = _DATA_DIR / "sessions"
        if not sessions_dir.exists():
            return
        now = time.time()
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            age_hours = (now - session_dir.stat().st_mtime) / 3600
            if age_hours > 24:
                shutil.rmtree(session_dir, ignore_errors=True)

    async def _send(self, data: dict) -> None:
        try:
            await self.ws.send_json(data)
        except Exception:
            self._cancelled = True

    def cancel(self) -> None:
        self._cancelled = True
        self._chat_manager.on_disconnect()
