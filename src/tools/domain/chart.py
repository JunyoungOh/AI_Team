"""Chart rendering tools — QuickChart (Tier 1, no API key).

Generates chart image URLs from Chart.js configurations.
Workers embed these URLs in their reports for visualization.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any


async def quickchart_render(
    chart_config: str,
    width: int = 600,
    height: int = 400,
    background_color: str = "white",
    device_pixel_ratio: float = 2.0,
) -> str:
    """Generate a chart image URL from a Chart.js configuration.

    Args:
        chart_config: Chart.js configuration as JSON string.
            Example: {"type":"bar","data":{"labels":["A","B"],"datasets":[{"data":[10,20]}]}}
        width: Image width in pixels.
        height: Image height in pixels.
        background_color: Background color (CSS color name or hex).
        device_pixel_ratio: Retina scaling factor (2.0 for sharp images).

    Returns:
        QuickChart image URL (PNG).
    """
    # Validate JSON
    try:
        parsed = json.loads(chart_config) if isinstance(chart_config, str) else chart_config
    except json.JSONDecodeError as e:
        return f"Error: Invalid Chart.js JSON — {e}"

    # Compact JSON for URL
    compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    encoded = urllib.parse.quote(compact, safe="")

    url = (
        f"https://quickchart.io/chart"
        f"?c={encoded}"
        f"&w={width}&h={height}"
        f"&bkg={urllib.parse.quote(background_color)}"
        f"&devicePixelRatio={device_pixel_ratio}"
        f"&f=png"
    )

    # QuickChart URL length limit is ~16,000 chars for GET
    if len(url) > 16000:
        return (
            "Error: Chart config too large for GET URL. "
            "Simplify the chart (fewer data points or shorter labels)."
        )

    return url


QUICKCHART_TOOL: dict[str, Any] = {
    "name": "quickchart_render",
    "description": (
        "차트 이미지 생성 — Chart.js 설정을 PNG 이미지 URL로 변환. "
        "bar, line, pie, radar, doughnut 등 모든 Chart.js 타입 지원. "
        "반환된 URL을 리포트에 삽입하면 차트 이미지가 표시됨."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_config": {
                "type": "string",
                "description": (
                    'Chart.js 설정 JSON 문자열. '
                    '예: {"type":"bar","data":{"labels":["Q1","Q2","Q3","Q4"],'
                    '"datasets":[{"label":"매출","data":[100,150,200,180]}]}}'
                ),
            },
            "width": {
                "type": "integer",
                "description": "이미지 너비 (기본: 600)",
            },
            "height": {
                "type": "integer",
                "description": "이미지 높이 (기본: 400)",
            },
            "background_color": {
                "type": "string",
                "description": "배경색 (기본: white)",
            },
        },
        "required": ["chart_config"],
    },
}
