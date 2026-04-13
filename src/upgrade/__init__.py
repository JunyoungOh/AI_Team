"""강화소 — 기존 로컬 앱 자동 업그레이드 모드.

흐름:
  1. 사용자가 폴더 경로 + 지시사항 입력
  2. 원본 폴더의 복사본 자동 생성 (backup.py)
  3. 단일 CLI 세션으로 앱 분석 + 명확화 질문 생성 (runner.analyze_and_clarify)
  4. 사용자 답변 수신
  5. 사용자 폴더에서 개발 세션 실행 (runner.run_upgrade_dev)
     - PROGRESS_UPGRADE.md 기반 handoff
     - rate limit 시 자동 재개
  6. 완료 리포트 생성 (runner.run_upgrade_report)
"""
