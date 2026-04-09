---
name: xlsx-to-markdown-table
description: xlsx 엑셀 파일의 첫 번째 시트를 마크다운 표(table) 형식으로 변환합니다.
---

# xlsx → 마크다운 표 변환

사용자가 제공한 xlsx 파일의 첫 번째 시트를 읽어 마크다운 표로 변환합니다.

## 절차

1. 사용자가 제공한 경로의 xlsx 파일을 확인합니다.
2. 아래 Python 스크립트를 Bash 도구로 실행합니다. `FILE_PATH_HERE` 부분을 실제 경로로 교체합니다.
3. 출력된 마크다운 표를 채팅창에 바로 붙여 출력합니다.

## Python 스크립트

```python
import sys

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl이 설치되어 있지 않습니다. 터미널에서 'pip install openpyxl'을 실행해주세요.")
    sys.exit(1)

from openpyxl import load_workbook

FILE_PATH = "FILE_PATH_HERE"
MAX_ROWS = 100

wb = load_workbook(FILE_PATH, data_only=True)
ws = wb.worksheets[0]

rows = list(ws.iter_rows(values_only=True))
total_rows = len(rows)

if total_rows == 0:
    print("(시트가 비어 있습니다.)")
    sys.exit(0)

header_row = rows[0]
data_rows = rows[1:MAX_ROWS + 1] if total_rows > MAX_ROWS + 1 else rows[1:]
actual_data_count = total_rows - 1  # 헤더 제외

def cell_to_str(value):
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\n", "<br>").replace("\r", "")
    s = s.replace("|", "\\|")
    return s

def make_row(cells):
    return "| " + " | ".join(cell_to_str(c) for c in cells) + " |"

def make_separator(n):
    return "| " + " | ".join(["---"] * n) + " |"

col_count = len(header_row)

print(make_row(header_row))
print(make_separator(col_count))
for row in data_rows:
    padded = list(row) + [None] * (col_count - len(row))
    print(make_row(padded[:col_count]))

if actual_data_count > MAX_ROWS:
    print(f"\n*(총 {actual_data_count}행 중 {MAX_ROWS}행 표시)*")
```

## 변환 규칙

- **헤더**: 첫 번째 행을 항상 헤더로 처리합니다.
- **빈 셀**: 빈 문자열로 처리합니다 (공백).
- **줄바꿈**: 셀 내 `\n`은 `<br>`로 치환합니다.
- **숫자/날짜/수식**: 셀의 표시값을 문자열로 변환합니다. 수식은 마지막으로 계산된 값을 사용합니다.
- **파이프 문자(`|`)**: 마크다운 표 구분자와 충돌하지 않도록 `\|`로 이스케이프합니다.
- **100행 초과**: 첫 100행(헤더 제외)만 변환하고 마지막에 `*(총 N행 중 100행 표시)*` 안내를 추가합니다.

## 출력

변환된 마크다운 표를 채팅창에 바로 출력합니다. 별도 파일로 저장하지 않습니다.

## 오류 처리

- **파일을 찾을 수 없는 경우**: 사용자에게 경로가 올바른지 확인하도록 안내합니다.
- **openpyxl 미설치**: `pip install openpyxl` 실행을 안내합니다.
- **빈 시트**: "(시트가 비어 있습니다.)" 메시지를 출력합니다.
