"""강화소 폴더 백업 유틸.

사용자가 지정한 폴더를 수정하기 전에, 전체 복사본을 만들어 두어
실수로 망가져도 언제든 롤백 가능하게 한다.

복사본은 원본 폴더 옆(형제 위치)에 `<원본이름>-backup-<timestamp>/` 형태로 만든다.

설계 고민:
  - React/Node 프로젝트의 node_modules는 수 GB → 복사 시간 폭증
  - Python 프로젝트의 .venv도 수백 MB
  - .git 폴더는 사이즈가 크고 어차피 버전관리에 있으므로 복사 불필요
  - 빌드 산출물(dist, build)은 재생성 가능하므로 불필요
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path


# 사용자 결정사항:
#   - .git 제외 (복사 불필요, 원본에 남아있음)
#   - .env 는 포함 (로컬 전용 앱, 민감 정보 백업 필요)
#   - 로그/캐시/빌드 산출물은 제외 (재생성 가능)

EXCLUDE_PATTERNS: list[str] = [
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".next",
    "dist",
    "build",
    ".cache",
    "*.log",
    ".DS_Store",
]


def create_backup(source_folder: str) -> str:
    """원본 폴더를 EXCLUDE_PATTERNS 제외하고 형제 위치에 복사.

    Returns:
        생성된 백업 폴더의 절대경로

    Raises:
        ValueError: 원본 폴더가 존재하지 않음
        FileExistsError: 백업 폴더가 이미 존재함 (동시 요청 방지)
    """
    src = Path(source_folder).expanduser().resolve()
    if not src.exists():
        raise ValueError(f"원본 폴더가 존재하지 않습니다: {src}")
    if not src.is_dir():
        raise ValueError(f"폴더가 아닙니다: {src}")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_name = f"{src.name}-backup-{timestamp}"
    dst = src.parent / backup_name

    if dst.exists():
        raise FileExistsError(f"백업 폴더가 이미 존재합니다: {dst}")

    ignore = shutil.ignore_patterns(*EXCLUDE_PATTERNS) if EXCLUDE_PATTERNS else None
    shutil.copytree(src, dst, ignore=ignore, symlinks=False)
    return str(dst)


def validate_target_folder(path: str) -> tuple[bool, str]:
    """대상 폴더가 강화소 작업에 적합한지 검증.

    금지 규칙:
      - 루트 '/' 또는 시스템 경로 (/System, /usr, /bin, /etc 등)
      - 홈 디렉토리 최상위 자체
      - 존재하지 않는 경로

    Returns:
        (is_valid, error_message)
    """
    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return False, f"경로 해석 실패: {e}"

    if not p.exists():
        return False, f"폴더가 존재하지 않습니다: {p}"
    if not p.is_dir():
        return False, f"폴더가 아닙니다: {p}"

    forbidden_prefixes = ("/System", "/usr", "/bin", "/sbin", "/etc", "/private/var", "/Library")
    path_str = str(p)
    for prefix in forbidden_prefixes:
        if path_str == prefix or path_str.startswith(prefix + "/"):
            return False, f"시스템 경로는 허용되지 않습니다: {prefix}"

    if path_str == "/" or path_str == str(Path.home()):
        return False, "루트 또는 홈 디렉토리 자체는 허용되지 않습니다"

    return True, ""
