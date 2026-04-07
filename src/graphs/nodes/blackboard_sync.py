"""Node: blackboard_sync — worker findings → Blackboard 수집.

worker_execution 완료 후 실행.
모든 워커의 labeled_findings를 파싱하여 CollectionBlackboard에 축적.
이미 Blackboard에 있는 데이터는 보존 (Analyst 루프의 2차 수집분 추가).
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage

from src.models.state import EnterpriseAgentState
from src.utils.collection_blackboard import CollectionBlackboard
from src.utils.logging import get_logger

logger = get_logger(agent_id="blackboard_sync")


def blackboard_sync(state: EnterpriseAgentState) -> dict:
    """워커 결과에서 labeled_findings를 추출하여 Blackboard에 동기화."""

    # 기존 Blackboard 복원 (Analyst 루프 시 이전 데이터 보존)
    bb = CollectionBlackboard.deserialize(state.get("collection_blackboard"))

    workers = state.get("workers", [])
    total_synced = 0
    workers_synced = 0

    for worker in workers:
        worker_id = worker.get("worker_id", "unknown")
        worker_domain = worker.get("worker_domain", "unknown")
        execution_result = worker.get("execution_result", "")

        if not execution_result or execution_result.startswith("["):
            # 실패한 워커 ([Execution failed:...]) 스킵
            continue

        # JSON 파싱 → labeled_findings 추출
        findings = _extract_findings(execution_result, worker_id)
        if not findings:
            logger.warning(f"[Sync] {worker_id}: labeled_findings 없음")
            continue

        count = bb.write_findings(worker_id, worker_domain, findings)
        total_synced += count
        workers_synced += 1

    summary = bb.get_summary()
    logger.info(f"[Sync] 완료: {workers_synced}명 워커에서 {total_synced}건 동기화. {summary}")

    logger.info(f"[Sync] UI 이벤트: {total_synced}건 수집 완료")

    return {
        "collection_blackboard": bb.serialize(),
        "collection_metadata": bb.stats(),
        "messages": [AIMessage(content=f"[Blackboard Sync] {summary}")],
    }


def _extract_findings(execution_result: str, worker_id: str) -> list[dict]:
    """execution_result에서 labeled_findings를 안전하게 추출.

    워커 출력 구조 (실제 관찰):
    외부 JSON = {"result_summary": "대화텍스트...", "deliverables": ["대화+코드블록"], "labeled_findings": []}
    - 최상위 labeled_findings는 빈 배열 (Claude streaming 합침 결과)
    - 실제 데이터는 deliverables[0] 안의 ```json 코드블록에 내장

    추출 전략:
    1차: 최상위 labeled_findings (비어있지 않으면 사용)
    2차: deliverables 내 텍스트에서 ```json 코드블록 추출
    3차: 전체 텍스트에서 ```json 코드블록 추출
    4차: "labeled_findings" 키 포함 { } 블록 브루트포스
    """
    # 1차: 외부 JSON의 최상위 labeled_findings
    outer_data = None
    try:
        outer_data = json.loads(execution_result)
        findings = outer_data.get("labeled_findings", [])
        if isinstance(findings, list) and findings:
            return findings
    except (json.JSONDecodeError, TypeError):
        pass

    # 2차: deliverables 내 텍스트에서 코드블록 추출
    if outer_data and isinstance(outer_data.get("deliverables"), list):
        for item in outer_data["deliverables"]:
            if not isinstance(item, str):
                continue
            findings = _find_in_text(item)
            if findings:
                return findings

    # 3차: 전체 execution_result에서 코드블록 추출
    findings = _find_in_text(execution_result)
    if findings:
        return findings

    logger.warning(f"[Sync] {worker_id}: labeled_findings 추출 실패 (모든 전략 시도)")
    return []


def _find_in_text(text: str) -> list[dict]:
    """텍스트에서 labeled_findings를 포함하는 JSON을 추출."""
    # ```json 코드블록 검색
    json_blocks = re.findall(r"```json\s*\n?(.*?)```", text, re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            findings = data.get("labeled_findings", [])
            if isinstance(findings, list) and findings:
                return findings
        except (json.JSONDecodeError, TypeError):
            continue

    # "labeled_findings" 포함 { } 블록 브루트포스
    if '"labeled_findings"' not in text:
        return []

    for match in re.finditer(r'\{', text):
        start = match.start()
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        data = json.loads(candidate)
                        findings = data.get("labeled_findings", [])
                        if isinstance(findings, list) and findings:
                            return findings
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
    return []
