"""
스킬 만들기 — 한국어 진입 레이어 (Option B)

설계 결정 (2026-04-09):
  공식 `skill-creator` 스킬이 이미 자체 인터뷰·반복 개선 루프를 갖고 있으므로,
  우리 레이어는 **얇은 한국어 진입 UX**만 담당합니다.

이 모듈의 역할 — 딱 2가지:
  1) 사용자의 첫 한국어 설명을 받는다
  2) skill-creator에 넘기기 전에 "한국어 인사 + 기존 스킬 검색 의사 확인"
     질문을 1개만 던진다

본격적인 스킬 인터뷰(트리거·입출력·테스트 케이스 등)는 **skill-creator에
위임**합니다. 그때 skill-creator에게는 "사용자와는 한국어로 대화하라"는
메타 지시를 시스템 프롬프트에 주입해서 언어 일관성을 유지합니다.

관련 파일:
  - src/graphs/nodes/single_session.py  (CLI 싱글세션 패턴 참고)
  - src/utils/claude_code.py            (Claude Code CLI 브리지)
  - src/skill_builder/skill_creator_bridge.py (후속 구현)
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────
# 한국어 진입 멘트 — 사용자가 첫 입력을 제출한 직후 보여줌
# ─────────────────────────────────────────────────────────────

ENTRY_GREETING = """\
좋아요, "{user_first_description}" 스킬을 만들어볼게요.

먼저 이미 비슷한 일을 하는 검증된 스킬이 있는지 확인해볼게요.
"""


SKIP_SEARCH_REASONS = """\
검색을 건너뛰고 싶다면 보통 이런 경우예요:
  - 완전히 새로운 워크플로우라 기존 스킬에 없을 게 확실할 때
  - 특정 내부 문서·데이터에 강하게 결합된 스킬일 때
  - 이미 한 번 검색해봤는데 없었을 때

그게 아니면 검색부터 하는 걸 추천합니다.
"""


# ─────────────────────────────────────────────────────────────
# skill-creator에게 전달할 메타 지시 — 이 스킬의 기본 언어가 영어라서
# 사용자와의 대화를 한국어로 진행하도록 강제합니다.
# ─────────────────────────────────────────────────────────────

SKILL_CREATOR_HANDOFF_DIRECTIVE = """\
You are about to invoke the `skill-creator` skill on behalf of a Korean-speaking
vibe-coder. Follow skill-creator's standard interview and iteration loop, BUT:

1. Conduct ALL user-facing communication in **Korean (한국어)**. The skill's
   internal reasoning can remain in English, but every question to the user
   and every explanation MUST be in Korean.
2. Avoid developer jargon unless the user demonstrates familiarity. Terms like
   "SKILL.md", "YAML frontmatter", "JSON schema" should be explained with a
   simple analogy on first use.
3. **ALWAYS run skill-creator's standard intake interview in Korean, even when
   the user's first description seems clear.** Translate and adapt skill-
   creator's standard 4 intake questions (목적, 입력/출력, 제약, 예시 시나리오
   등) into natural Korean. Ask them as ONE message with numbered items so the
   user can answer everything in a single textarea reply.

   - Do NOT skip the interview just because the description "looks obvious" —
     short descriptions almost always hide important detail (e.g. "글자수
     세기" doesn't say whether spaces count, what input format is expected,
     what to return for empty input).
   - Do NOT immediately jump to creating the skill on the first turn. The
     interview turn is mandatory.
   - The user's textarea reply will arrive as a single user message — parse
     it carefully and use ALL the answers when drafting the skill.
4. The final artifact MUST be a Claude Code **skill** saved under
   `{install_root}/skill-tab-{{slug}}/` (an absolute project-internal path
   that the app passes you via `--add-dir`). The `skill-tab-` prefix is
   required so our app can identify skills it created. **Do NOT save under
   `~/.claude/skills/`** — that is a Claude Code system directory and writes
   there are blocked in headless mode.
5. You are creating a SKILL, not a Claude Code subagent. Do NOT write to
   `~/.claude/agents/`. Do NOT generate subagent frontmatter. Do NOT invoke
   the Task/Agent tool to create a subagent.
6. If external data sources are needed, record required MCPs in a metadata
   file `skill_metadata.json` alongside SKILL.md. Do NOT modify the user's
   global `settings.json` directly.
7. **DRAFT → APPROVAL → SAVE FLOW (CRITICAL — read carefully).**
   You have FULL write permission to `{install_root}/skill-tab-{{slug}}/` and
   any subdirectories you create inside it. The `--add-dir` flag has already
   been passed to grant access. **However**, you must NOT immediately write
   the file. Instead follow this 3-stage flow:

   **Stage 1 — Show the draft (chat only, no Write tool yet):**
   - In a single Korean message, show:
     a) The proposed `description` (the YAML frontmatter `description:` value
        — must be neutral per rule 8 below)
     b) A short outline of the skill body (2~5 bullet points describing
        sections: 동작, 입력, 출력, 예시, 주의사항 등)
     c) Any MCP requirements (or "외부 도구 없음")
   - End the message with: **"이대로 만들어드릴까요? 수정하고 싶은 부분이
     있으면 알려주세요."**
   - Do NOT call the Write tool in this stage.
   - Do NOT include the full SKILL.md body — just the outline.

   **Stage 2 — Wait for user response:**
   - If the user says "수정", "고쳐줘", "다시", "X 부분을 …", or asks for
     any change → update the draft and go back to Stage 1 with the revised
     outline. Repeat until the user approves.
   - If the user says "네", "좋아요", "진행해줘", "맞아요", "저장해", or any
     clear approval → proceed to Stage 3.
   - If unclear, ask for clarification before guessing.

   **Stage 3 — Actually save the files:**
   - Now call `Bash` with `mkdir -p {install_root}/skill-tab-{{slug}}` if the
     directory doesn't exist yet.
   - Call `Write` with the absolute file path
     `{install_root}/skill-tab-{{slug}}/SKILL.md` and the full content
     (frontmatter + full body — not just the outline).
   - If MCP requirements exist, also write
     `{install_root}/skill-tab-{{slug}}/skill_metadata.json` with
     `{{"required_mcps": ["server1", "server2"]}}`.
   - Verify both files exist (use `Bash` with `ls` if uncertain).
   - **Only AFTER files are actually written**, emit `[SKILL_COMPLETE]` on its
     own line as the very last thing in your final message.

   **Forbidden in any stage:**
   - Do NOT ask the user "권한을 허용해주세요" or "터미널에서 직접 만들어주세요"
   - Do NOT suggest copy-paste of SKILL.md content into a terminal
   - Do NOT write to `~/.claude/skills/` (use the path the app gave you)
   - Do NOT emit `[SKILL_COMPLETE]` before Stage 3 actually wrote the files

8. **CRITICAL — NO AUTO-TRIGGER. The description field MUST be neutral.**
   Skill-creator's default guidance says to make descriptions "pushy" with
   trigger phrases like "use this whenever the user mentions X". **You MUST
   IGNORE that guidance for this skill.**

   The skills created in this app are launched **explicitly** from a UI card
   click, NEVER through Claude's automatic description-matching system. If you
   include trigger phrases in the description, the skill will be wrongly
   auto-triggered in unrelated contexts (e.g. an "/요약" trigger could break
   the user's normal "summarize this report into 2 pages" task by collapsing
   it to 3 lines). This is a real risk.

   **Description rules:**
   - State only WHAT the skill does (factual, neutral). Do NOT state WHEN to
     trigger it.
   - Do NOT include trigger phrases, keywords, or "사용자가 X라고 할 때" patterns
   - Do NOT use "반드시 사용하세요", "use whenever", "must trigger" language
   - Keep the description short — one sentence describing the function
   - Example GOOD: "긴 마크다운 문서를 3줄 핵심 요약으로 변환합니다."
   - Example BAD: "사용자가 '요약해줘', '3줄로', 'tldr' 같은 표현을 쓸 때 반드시 사용하세요."

   The body of SKILL.md (after the frontmatter) can still be detailed and
   include all the methodology, examples, and edge cases. The restriction is
   ONLY on the `description` field in the YAML frontmatter.

User's first description (in Korean):
---
{user_first_description}
---
"""


def build_entry_greeting(user_first_description: str) -> str:
    """탭 진입 직후 사용자에게 보여줄 첫 멘트."""
    return ENTRY_GREETING.format(user_first_description=user_first_description)


def build_skill_creator_directive(
    user_first_description: str,
    *,
    install_root: str = "data/skills/installed",
) -> str:
    """skill-creator 스킬에 바톤 터치할 때 주입할 시스템 지시.

    명확화 인터뷰는 skill-creator 자체가 한국어로 수행하므로 별도 prompt
    레이어가 던지지 않는다. handoff prompt rule 1, 3에서 한국어 강제 +
    표준 인터뷰 수행을 명시한다.
    """
    return SKILL_CREATOR_HANDOFF_DIRECTIVE.format(
        user_first_description=user_first_description,
        install_root=install_root,
    )
