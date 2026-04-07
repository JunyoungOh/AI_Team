import pytest


def test_recommend_endpoint_exists():
    from src.ui.server import app
    routes = [r.path for r in app.routes]
    assert '/api/discussion/recommend-participants' in routes

def test_recommend_fallback_on_no_bridge():
    """When API call fails, should return fallback participants."""
    import asyncio
    from src.ui.server import _recommend_participants
    # This will fail (no API key in test env) and should return fallback
    result = asyncio.run(
        _recommend_participants("테스트 주제", "free", "basic", 3)
    )
    assert isinstance(result, list)
    assert len(result) >= 2
    for p in result:
        assert "name" in p
        assert "persona" in p
