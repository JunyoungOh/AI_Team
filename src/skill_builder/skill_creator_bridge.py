"""공식 skill-creator 스킬 호출을 위한 프롬프트 빌더.

세션 resume 루프 자체는 runner.py에서 관리한다 (여기는 프롬프트만).
skill-creator 기본 언어가 영어이므로 한국어로 사용자와 대화하도록
메타 지시를 주입하고, 루프 종료를 감지할 완료 토큰을 명시한다.
"""

from __future__ import annotations

from src.skill_builder.clarification_questions import (
    build_skill_creator_directive,
)


_COMPLETION_INSTRUCTION = """

## Completion Signal

When you have finished creating the skill (Stage 3 of rule 7 — files actually
written to disk) and the user has approved the draft:
1. Verify `{install_root}/skill-tab-{slug}/SKILL.md` exists on disk
2. End your final message with the exact token on its own line: [SKILL_COMPLETE]

Do NOT emit [SKILL_COMPLETE] until the file is actually written. Our app
polls this token to know when the session is done. Emitting it prematurely
(e.g. while still showing the draft in Stage 1) will cause the session to
end before the skill is saved.
"""


def build_handoff_system_prompt(
    user_description: str,
    slug: str,
    *,
    install_root: str = "data/skills/installed",
) -> str:
    """skill-creator 호출 시 주입할 완성된 시스템 프롬프트.

    핵심 책임:
      1) skill-creator 스킬을 써야 한다고 명시 (clarification_questions에서)
      2) 사용자와의 대화는 한국어 (clarification_questions에서)
      3) 저장 경로 `{install_root}/skill-tab-{slug}/` 강제 (data/skills/installed/)
      4) skill-creator의 한국어 인터뷰를 단일 세션 안에서 강제 수행 (rule 3)
      5) subagent 생성 금지 (clarification_questions에서)
      6) MCP 요구사항은 skill_metadata.json에 별도 기록
      7) 초안 → 승인 → 저장 3단계 흐름 명시 (rule 7)
      8) 완료 시 [SKILL_COMPLETE] 토큰 emit (여기서)
    """
    base = build_skill_creator_directive(
        user_description,
        install_root=install_root,
    )
    # base는 {{slug}} (literal — format에서 escape됨) 만 남아있음
    base = base.replace("{slug}", slug)
    completion = _COMPLETION_INSTRUCTION.format(
        install_root=install_root,
        slug=slug,
    )
    return base + completion
