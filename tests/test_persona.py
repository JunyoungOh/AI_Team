"""Tests for Persona Workshop."""
import pytest
from src.persona.models import PersonaDB


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    pdb = PersonaDB(db_path=db_path)
    # Create stub users table for JOIN queries (in production UserDB creates the full table)
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT, display_name TEXT)")
    conn.close()
    return pdb


class TestPersonaDB:
    def test_create_and_get(self, db):
        p = db.create(user_id="u1", name="일론 머스크", summary="Tesla CEO",
                       persona_text="당신은...", source="web")
        assert p["id"]
        assert p["name"] == "일론 머스크"
        got = db.get(p["id"], user_id="u1")
        assert got["persona_text"] == "당신은..."

    def test_get_wrong_user(self, db):
        p = db.create(user_id="u1", name="Test", summary="", persona_text="", source="web")
        assert db.get(p["id"], user_id="u2") is None

    def test_list_by_user(self, db):
        db.create(user_id="u1", name="A", summary="", persona_text="", source="web")
        db.create(user_id="u1", name="B", summary="", persona_text="", source="web")
        db.create(user_id="u2", name="C", summary="", persona_text="", source="web")
        items = db.list(user_id="u1")
        assert len(items) == 2

    def test_update_with_ownership(self, db):
        p = db.create(user_id="u1", name="Old", summary="", persona_text="old", source="web")
        ok = db.update(p["id"], user_id="u1", persona_text="new")
        assert ok is True
        got = db.get(p["id"], user_id="u1")
        assert got["persona_text"] == "new"

    def test_update_wrong_user(self, db):
        p = db.create(user_id="u1", name="Test", summary="", persona_text="", source="web")
        ok = db.update(p["id"], user_id="u2", persona_text="hacked")
        assert ok is False

    def test_delete_with_ownership(self, db):
        p = db.create(user_id="u1", name="Test", summary="", persona_text="", source="web")
        ok = db.delete(p["id"], user_id="u1")
        assert ok is True
        assert db.get(p["id"], user_id="u1") is None

    def test_delete_wrong_user(self, db):
        p = db.create(user_id="u1", name="Test", summary="", persona_text="", source="web")
        ok = db.delete(p["id"], user_id="u2")
        assert ok is False

    def test_count_limit(self, db):
        for i in range(50):
            db.create(user_id="u1", name=f"P{i}", summary="", persona_text="", source="web")
        assert db.count(user_id="u1") == 50

    def test_preview_token_cache(self, db):
        token = db.store_preview("u1", {"results": "data"})
        assert token
        data = db.get_preview(token, "u1")
        assert data["results"] == "data"

    def test_preview_token_wrong_user(self, db):
        token = db.store_preview("u1", {"results": "data"})
        assert db.get_preview(token, "u2") is None


class TestDiscussionIntegration:
    def test_participant_persona_id_field(self):
        from src.discussion.config import Participant
        p = Participant(id="a", name="A", persona="placeholder", persona_id="abc123")
        assert p.persona_id == "abc123"

    def test_participant_without_persona_id(self):
        from src.discussion.config import Participant
        p = Participant(id="a", name="A", persona="test")
        assert p.persona_id is None


class TestPersonaPrompts:
    def test_prompts_exist(self):
        from src.persona.prompts import PERSONA_SYNTHESIS_SYSTEM, INTERVIEWER_SYSTEM, SUFFICIENCY_SYSTEM
        assert "사고방식" in PERSONA_SYNTHESIS_SYSTEM
        assert "인터뷰어" in INTERVIEWER_SYSTEM
        assert "충분" in SUFFICIENCY_SYSTEM


class TestChatEngineReset:
    def test_reset_clears_history(self):
        from src.secretary.chat_engine import ChatEngine, ChatMessage
        from src.secretary.config import SecretaryConfig
        engine = ChatEngine(config=SecretaryConfig(), session_tag="test", session_id="", user_id="")
        engine.history.append(ChatMessage(role="user", content="hello"))
        assert len(engine.history) == 1
        engine.reset()
        assert len(engine.history) == 0

    def test_reset_changes_prompt(self):
        from src.secretary.chat_engine import ChatEngine
        from src.secretary.config import SecretaryConfig
        engine = ChatEngine(config=SecretaryConfig(), session_tag="test", session_id="", user_id="")
        assert engine._system_prompt_template is None
        engine.reset(system_prompt_template="new prompt {context}")
        assert engine._system_prompt_template == "new prompt {context}"

    def test_reset_to_default(self):
        from src.secretary.chat_engine import ChatEngine
        from src.secretary.config import SecretaryConfig
        engine = ChatEngine(config=SecretaryConfig(), session_tag="test", session_id="", user_id="",
                            system_prompt_template="custom {context}")
        assert engine._system_prompt_template == "custom {context}"
        engine.reset(system_prompt_template=None)
        assert engine._system_prompt_template is None


class TestSecretaryPersona:
    def test_build_persona_prompt(self):
        from src.secretary.session import _build_persona_prompt
        result = _build_persona_prompt("나는 테스트입니다. {이름} 확인")
        assert "{context}" in result
        assert "{{이름}}" in result  # curly braces escaped

    def test_build_persona_prompt_none(self):
        from src.secretary.session import _build_persona_prompt
        result = _build_persona_prompt(None)
        assert result is None

    def test_build_persona_prompt_empty(self):
        from src.secretary.session import _build_persona_prompt
        result = _build_persona_prompt("")
        assert result is None


class TestAdvisoryPrompts:
    def test_advisory_prompt_exists(self):
        from src.persona.prompts import ADVISORY_COMMENT_SYSTEM, SCENARIO_ROLEPLAY_SYSTEM
        assert "{persona_name}" in ADVISORY_COMMENT_SYSTEM
        assert "{persona_text}" in ADVISORY_COMMENT_SYSTEM
        assert "코멘트" in ADVISORY_COMMENT_SYSTEM or "보고서" in ADVISORY_COMMENT_SYSTEM
        assert "시나리오" in SCENARIO_ROLEPLAY_SYSTEM
        assert "롤플레이" in SCENARIO_ROLEPLAY_SYSTEM or "대응" in SCENARIO_ROLEPLAY_SYSTEM

    def test_advisory_module_imports(self):
        from src.persona.advisory import generate_advisory_comments, generate_scenario_roleplay
        assert callable(generate_advisory_comments)
        assert callable(generate_scenario_roleplay)


class TestPersonaSharing:
    def test_share_toggle(self, db):
        p = db.create(user_id="u1", name="A", summary="", persona_text="text", source="web")
        assert db.toggle_share(p["id"], "u1", True) is True
        got = db.get(p["id"], "u1")
        assert got["shared"] == 1

    def test_share_toggle_wrong_user(self, db):
        p = db.create(user_id="u1", name="A", summary="", persona_text="text", source="web")
        assert db.toggle_share(p["id"], "u2", True) is False

    def test_list_shared_excludes_own(self, db):
        p1 = db.create(user_id="u1", name="A", summary="", persona_text="text", source="web")
        p2 = db.create(user_id="u2", name="B", summary="", persona_text="text", source="web")
        db.toggle_share(p1["id"], "u1", True)
        db.toggle_share(p2["id"], "u2", True)
        shared = db.list_shared("u1")
        assert len(shared) == 1
        assert shared[0]["name"] == "B"

    def test_use_toggle(self, db):
        p = db.create(user_id="u1", name="A", summary="", persona_text="text", source="web")
        db.toggle_share(p["id"], "u1", True)
        assert db.toggle_use("u2", p["id"], True) is True
        shared = db.list_shared("u2")
        assert shared[0]["used_by_me"] is True

    def test_use_toggle_off(self, db):
        p = db.create(user_id="u1", name="A", summary="", persona_text="text", source="web")
        db.toggle_share(p["id"], "u1", True)
        db.toggle_use("u2", p["id"], True)
        db.toggle_use("u2", p["id"], False)
        shared = db.list_shared("u2")
        assert shared[0]["used_by_me"] is False

    def test_list_usable(self, db):
        p1 = db.create(user_id="u1", name="Mine", summary="", persona_text="text", source="web")
        p2 = db.create(user_id="u2", name="Shared", summary="", persona_text="text", source="web")
        db.toggle_share(p2["id"], "u2", True)
        db.toggle_use("u1", p2["id"], True)
        usable = db.list_usable("u1")
        names = [p["name"] for p in usable]
        assert "Mine" in names
        assert "Shared" in names

    def test_list_usable_excludes_unused_shared(self, db):
        p = db.create(user_id="u2", name="NotUsed", summary="", persona_text="text", source="web")
        db.toggle_share(p["id"], "u2", True)
        usable = db.list_usable("u1")
        names = [p["name"] for p in usable]
        assert "NotUsed" not in names

    def test_unshare_removes_from_shared(self, db):
        p = db.create(user_id="u1", name="A", summary="", persona_text="text", source="web")
        db.toggle_share(p["id"], "u1", True)
        assert len(db.list_shared("u2")) == 1
        db.toggle_share(p["id"], "u1", False)
        assert len(db.list_shared("u2")) == 0

    def test_get_usable_own(self, db):
        p = db.create(user_id="u1", name="Mine", summary="", persona_text="text", source="web")
        got = db.get_usable(p["id"], "u1")
        assert got is not None
        assert got["name"] == "Mine"

    def test_get_usable_shared(self, db):
        p = db.create(user_id="u2", name="Other", summary="", persona_text="text", source="web")
        db.toggle_share(p["id"], "u2", True)
        db.toggle_use("u1", p["id"], True)
        got = db.get_usable(p["id"], "u1")
        assert got is not None
        assert got["name"] == "Other"

    def test_get_usable_shared_not_enabled(self, db):
        p = db.create(user_id="u2", name="Other", summary="", persona_text="text", source="web")
        db.toggle_share(p["id"], "u2", True)
        # u1 did NOT enable use
        got = db.get_usable(p["id"], "u1")
        assert got is None
