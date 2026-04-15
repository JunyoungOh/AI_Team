"""CORPCODE.xml ↔ 회사명 검색 인덱스.

Open DART는 모든 회사별 API가 8자리 ``corp_code``를 요구한다. 사용자는
"삼성전자"라는 이름으로 질문하므로 이름 → corp_code 해석층이 필수.

설계 원칙
---------
- CORPCODE.xml은 약 10만 회사를 담은 정적 인덱스 — 내용(공시자료)이
  아니라 식별자 매핑이라서 디스크 캐시해도 정보 신선도 함정에 걸리지
  않음. 7일마다 재다운로드.
- 부트스트랩은 ``CorpCodeIndex.get()`` 한 번의 await로 끝나며 이후는
  프로세스 싱글턴.
- 랭킹은 ``_score()`` 단일 함수에 격리되어 있어 실사용 테스트 후 가중치
  조정만으로 튜닝 가능. 로직 재작성 필요 없음.

랭킹 설계 (도메인 휴리스틱)
---------------------------
1. 6자리 숫자 쿼리 → 종목코드(stock_code) 직접 매칭, 랭킹 우회
2. 정확 일치 (10000점)
3. Prefix 매치 (5000 − name_len·5) — 한국 기업명은 브랜드+접미사 구조라
   prefix가 강한 신호 ("삼성전" → "삼성전자")
4. Reverse prefix (3000 − |q_len − n_len|·10) — 사용자가 "삼성전자주식
   회사"를 입력했을 때 "삼성전자" 매치
5. Substring (1000 − name_len·2) — "전자"로 "삼성전자" 찾기 (약한 신호)
6. Char overlap fallback — 위 어느 것도 안 될 때 자모 단위 유사도
7. 상장사 보너스 +500 — DART 사용자의 90%+가 상장사에 관심
8. 최근 수정 (2023년 이후) +100, 오래된 수정 (2020년 이전) −200 —
   살아있는 법인 우선
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from src.dart.client import DartAPIError, DartClient

logger = logging.getLogger(__name__)

_CACHE_PATH = Path("data/dart/corp_code.json")
_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7일

# 한국 법인명 정규화 — 비교 전에 제거할 접미사/기호
_SUFFIX_PATTERN = re.compile(
    r"(주식회사|株式會社|유한회사|유한책임회사|\(주\)|㈜|\(유\)|Co\.?,?\s*Ltd\.?|Corp\.?|Inc\.?)",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_TICKER_PATTERN = re.compile(r"^\d{6}$")


class CorpCodeIndex:
    """In-memory searchable index over DART's CORPCODE master."""

    _instance: "CorpCodeIndex | None" = None
    _lock = asyncio.Lock()

    def __init__(self, companies: list[dict[str, Any]], downloaded_at: str) -> None:
        self._companies = companies
        self._downloaded_at = downloaded_at
        self._by_stock_code: dict[str, dict[str, Any]] = {
            c["stock_code"]: c for c in companies if c.get("stock_code")
        }
        self._by_corp_code: dict[str, dict[str, Any]] = {
            c["corp_code"]: c for c in companies if c.get("corp_code")
        }

    # ── 부트스트랩 ─────────────────────────────

    @classmethod
    async def get(cls, client: DartClient | None = None) -> "CorpCodeIndex":
        """싱글턴 — 최초 호출 시 다운로드/캐시, 이후는 즉시 반환."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = await cls._bootstrap(client or DartClient())
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """테스트/재로드용 — 싱글턴 초기화."""
        cls._instance = None

    @classmethod
    async def _bootstrap(cls, client: DartClient) -> "CorpCodeIndex":
        if _CACHE_PATH.exists():
            age = time.time() - _CACHE_PATH.stat().st_mtime
            if age < _CACHE_TTL_SECONDS:
                try:
                    with _CACHE_PATH.open("r", encoding="utf-8") as f:
                        payload = json.load(f)
                    logger.info(
                        "CorpCodeIndex: 캐시 로드 %d개 (age=%.1f일)",
                        len(payload.get("companies", [])),
                        age / 86400,
                    )
                    return cls(payload["companies"], payload["downloaded_at"])
                except (json.JSONDecodeError, KeyError, OSError) as exc:
                    logger.warning("CorpCodeIndex 캐시 손상, 재다운로드: %s", exc)

        # 캐시 없음/만료/손상 → 새로 다운로드
        logger.info("CorpCodeIndex: Open DART에서 CORPCODE.xml 다운로드")
        zip_bytes = await client.get_corp_code_zip()
        companies = _parse_corpcode_zip(zip_bytes)
        downloaded_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())

        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(
                {"downloaded_at": downloaded_at, "companies": companies},
                f,
                ensure_ascii=False,
            )
        logger.info("CorpCodeIndex: %d개 회사 인덱싱 완료", len(companies))
        return cls(companies, downloaded_at)

    # ── 조회 API ──────────────────────────────

    @property
    def size(self) -> int:
        return len(self._companies)

    @property
    def downloaded_at(self) -> str:
        return self._downloaded_at

    def lookup_by_corp_code(self, corp_code: str) -> dict[str, Any] | None:
        return self._by_corp_code.get(corp_code)

    def lookup_by_ticker(self, stock_code: str) -> dict[str, Any] | None:
        return self._by_stock_code.get(stock_code)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """회사명/종목코드 → 상위 N개 후보 (점수 내림차순).

        Ticker 쿼리(6자리 숫자)는 direct lookup 후 단일 결과로 반환.
        """
        query = (query or "").strip()
        if not query:
            return []

        # 1. 종목코드 직행
        if _TICKER_PATTERN.match(query):
            hit = self._by_stock_code.get(query)
            return [hit] if hit else []

        # 2. 이름 랭킹
        q_norm = _normalize(query)
        if not q_norm:
            return []

        scored: list[tuple[float, dict[str, Any]]] = []
        for cand in self._companies:
            s = _score(q_norm, cand)
            if s > 0:
                scored.append((s, cand))
        scored.sort(key=lambda x: -x[0])
        return [cand for _, cand in scored[:limit]]


# ── 정규화 & 랭킹 ─────────────────────────────


def _normalize(name: str) -> str:
    """법인명 정규화 — 비교 전에 접미사/공백/대소문자 제거."""
    if not name:
        return ""
    s = _SUFFIX_PATTERN.sub("", name)
    s = _WHITESPACE.sub("", s)
    return s.lower()


def _score(q_norm: str, cand: dict[str, Any]) -> float:
    """단일 후보 점수. 가중치는 실사용 테스트 후 튜닝 대상."""
    name = cand.get("corp_name", "")
    n_norm = _normalize(name)
    if not n_norm:
        return 0.0

    score = 0.0

    if q_norm == n_norm:
        # 정확 일치 — 타의 추종을 불허
        score = 10_000
    elif n_norm.startswith(q_norm):
        # "삼성전" → "삼성전자" — 강한 신호, 짧을수록 본체
        score = 5_000 - len(n_norm) * 5
    elif q_norm.startswith(n_norm):
        # "삼성전자주식회사" → "삼성전자" — 법인명 풀네임 입력
        score = 3_000 - (len(q_norm) - len(n_norm)) * 10
    elif q_norm in n_norm:
        # "전자" → "삼성전자" — 약한 신호, 모호함 위험
        score = 1_000 - len(n_norm) * 2
    else:
        # 자모 단위 fallback — "하이닉스" 입력이 "sk하이닉스"를 찾을 때
        overlap = sum(1 for ch in q_norm if ch in n_norm)
        threshold = max(2, len(q_norm) // 2)
        if overlap >= threshold:
            score = 100 + overlap * 10

    if score <= 0:
        return 0.0

    # ── 보조 시그널 ────
    # 상장사 보너스 — DART 사용자 주 관심사
    if cand.get("stock_code"):
        score += 500

    # 법인 생사 시그널 — modify_date
    modify = cand.get("modify_date", "")
    if modify >= "20230101":
        score += 100
    elif modify and modify < "20200101":
        score -= 200

    return score


# ── CORPCODE.xml 파싱 ─────────────────────────


def _parse_corpcode_zip(zip_bytes: bytes) -> list[dict[str, Any]]:
    """ZIP 내부 CORPCODE.xml 파싱 → 정규화된 딕셔너리 리스트."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
        if not xml_name:
            raise DartAPIError("CORPCODE ZIP 안에 XML 파일이 없습니다.")
        xml_bytes = zf.read(xml_name)

    root = ET.fromstring(xml_bytes)
    companies: list[dict[str, Any]] = []
    for node in root.findall("list"):
        corp_code = _text(node.find("corp_code"))
        if not corp_code:
            continue
        companies.append(
            {
                "corp_code": corp_code,
                "corp_name": _text(node.find("corp_name")),
                "corp_eng_name": _text(node.find("corp_eng_name")),
                "stock_code": _text(node.find("stock_code")),
                "modify_date": _text(node.find("modify_date")),
            }
        )
    return companies


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()
