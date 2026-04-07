"""Prompts for Breadth Research pipeline."""

TRANSLATE_QUERY_PROMPT = """\
다음 한국어 검색어를 영어 검색어로 변환하세요. 검색어만 반환하고 다른 텍스트는 포함하지 마세요.

한국어 검색어: {query}
"""

FILTER_CHUNKS_PROMPT = """\
다음 텍스트 조각들이 주어진 검색 주제와 관련이 있는지 판단하세요.

## 검색 주제
{sub_query}

## 텍스트 조각들
{chunks_text}

## 지시사항
각 텍스트 조각에 대해:
- 관련 있으면: 핵심 내용을 1-2문장으로 요약
- 관련 없으면: 건너뜀

관련 있는 조각의 요약만 반환하세요. 각 요약 앞에 [출처: URL] 태그를 붙이세요.
관련 있는 것이 하나도 없으면 "없음"이라고만 반환하세요.
"""

BREADTH_SYNTHESIS_PROMPT = """\
당신은 리서치 리포트 작성 전문가입니다.
아래 수집된 정보를 바탕으로 구조화된 리포트를 작성하세요.

## 원래 질문
{user_task}

## 도메인
{domain}

## 수집된 정보
{filtered_context}

## 작성 규칙
1. 수집된 정보만 사용하세요. 추측이나 자체 판단을 추가하지 마세요.
2. 출처 URL을 인라인으로 명시하세요.
3. report_html은 inline CSS로 작성 (외부 CSS 의존 금지).
4. <html>, <head>, <body>, <style> 태그 금지 — 외부 페이지에 삽입됨.
5. 라이트 테마: 흰색 배경, 텍스트 #1a1a1a, 보조 #555, 배경 #f8f9fa, 테두리 #e0e0e0.
6. 헤더/제목: #1a1a2e, 강조색: #0f3460.
7. 수치 데이터는 테이블/인라인 바 차트로 시각화.
8. key_findings의 category: fact, statistic, quote, analysis, recommendation, risk, opportunity.
9. key_findings의 importance: 5=핵심, 4=주요, 3=맥락, 2=부가, 1=배경.
10. 에이전트/도구/내부 프로세스를 절대 언급하지 마세요.

## 인라인 바 차트 예시
<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
  <span style="width:80px;font-size:13px;color:#555">항목</span>
  <div style="flex:1;background:#f0f0f0;border-radius:4px;height:20px">
    <div style="width:75%;background:linear-gradient(90deg,#4A90D9,#357ABD);height:100%;border-radius:4px;display:flex;align-items:center;padding-left:6px;color:#fff;font-size:11px">75%</div>
  </div>
</div>

## 구조 가이드
1. 리포트 제목 + 핵심 요약 (3-5문장)
2. 주요 발견사항 (데이터 중심, 시각화 활용)
3. 상세 분석 (섹션별 구조화)
4. 결론 및 시사점
5. 출처 목록
"""
