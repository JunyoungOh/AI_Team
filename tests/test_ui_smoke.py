# tests/test_ui_smoke.py
"""
UI 스모크 테스트 — 모듈 추출 리팩토링 안전망.
각 핵심 모드가 에러 없이 로딩되는지 확인.
"""
import pytest

pytestmark = pytest.mark.skipif(
    True,  # UI smoke tests require a running server + Playwright
    reason="UI smoke tests require running server (run manually with pytest -m ui)",
)

from playwright.sync_api import sync_playwright, expect
import time

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    # 콘솔 에러 수집
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.errors = errors
    yield page
    ctx.close()


def _login(page):
    """테스트용 로그인"""
    page.goto(BASE_URL)
    page.wait_for_selector("#auth-screen", state="visible", timeout=5000)
    # 이미 로그인된 경우 스킵
    if page.locator("#landing").is_visible():
        return
    page.fill("#entry-code", "test")
    page.click("#entry-submit")
    page.wait_for_selector("#landing", state="visible", timeout=10000)


class TestLandingPage:
    def test_landing_loads(self, page):
        """랜딩 페이지가 정상 로드되는지 확인"""
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        # auth 또는 landing이 표시되어야 함
        assert page.locator("#auth-screen").is_visible() or page.locator("#landing").is_visible()
        assert len(page.errors) == 0, f"Console errors: {page.errors}"


class TestModeCards:
    def test_mode_cards_visible(self, page):
        """모드 카드가 정상 표시되는지 확인"""
        _login(page)
        cards = page.locator(".mode-card:visible")
        assert cards.count() >= 3  # 최소 3개 모드 카드 표시

    def test_no_js_errors_on_landing(self, page):
        """랜딩 페이지에서 JS 에러 없음"""
        _login(page)
        page.wait_for_timeout(2000)
        assert len(page.errors) == 0, f"Console errors: {page.errors}"


class TestModeEntry:
    """각 핵심 모드가 실제로 시작되는지 확인 — 카드 클릭 후 모드별 UI 요소 표시"""

    def test_company_mode_starts(self, page):
        _login(page)
        page.click('[data-mode="company"]')
        page.wait_for_selector("#cv-wrap", state="visible", timeout=10000)
        assert len(page.errors) == 0, f"Company errors: {page.errors}"

    def test_discussion_mode_starts(self, page):
        _login(page)
        page.click('[data-mode="discussion"]')
        page.wait_for_selector("#disc-app", state="visible", timeout=10000)
        assert len(page.errors) == 0, f"Discussion errors: {page.errors}"

    def test_secretary_mode_starts(self, page):
        _login(page)
        page.click('[data-mode="secretary"]')
        page.wait_for_selector("#sec-app", state="visible", timeout=10000)
        assert len(page.errors) == 0, f"Secretary errors: {page.errors}"

    def test_foresight_mode_starts(self, page):
        _login(page)
        page.click('[data-mode="foresight"]')
        page.wait_for_selector("#foresight-app", state="visible", timeout=10000)
        assert len(page.errors) == 0, f"Foresight errors: {page.errors}"
