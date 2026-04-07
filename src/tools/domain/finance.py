"""Finance domain tools (Tier 1 — no API key required + Tier 2 — API key required).

Tier 1 (7 tools, no API key):
- world_bank_data: Global macro indicators (200 countries)
- exchange_rate: Real-time currency exchange rates (160+ currencies)
- imf_data: IMF World Economic Outlook
- dbnomics_data: ECB/OECD/Eurostat unified economic data
- pykrx_stock: Korean stock market (KOSPI/KOSDAQ)
- yfinance_data: Global stocks and financials
- fear_greed_index: Crypto Fear & Greed Index

Tier 2 (3 tools, API key required):
- dart_financial: Korean corporate financial statements (DART OpenAPI)
- ecos_data: Bank of Korea economic statistics (ECOS)
- kosis_data: Korean government statistics (KOSIS)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx


class ToolError(Exception):
    """Tool execution failed — returned as tool_result with is_error=True."""

def _get_http() -> httpx.AsyncClient:
    """Create a fresh AsyncClient — avoids cross-event-loop binding."""
    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


# ── World Bank ─────────────────────────────────────


async def world_bank_data(
    country: str = "KR",
    indicator: str = "NY.GDP.MKTP.CD",
    start_year: int = 2018,
    end_year: int = 2024,
) -> str:
    """Fetch World Bank indicator data for a country."""
    url = (
        f"https://api.worldbank.org/v2/country/{country}"
        f"/indicator/{indicator}"
        f"?date={start_year}:{end_year}&format=json&per_page=50"
    )
    resp = await _get_http().get(url)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list) or len(data) < 2:
        return json.dumps({"error": "No data found", "query": url}, ensure_ascii=False)

    records = [
        {
            "year": item.get("date"),
            "value": item.get("value"),
            "indicator": item.get("indicator", {}).get("value", indicator),
            "country": item.get("country", {}).get("value", country),
        }
        for item in data[1]
        if item.get("value") is not None
    ]
    return json.dumps(records, ensure_ascii=False)


WORLD_BANK_TOOL: dict[str, Any] = {
    "name": "world_bank_data",
    "description": (
        "세계은행 데이터 — 200개국 거시경제 지표 조회. "
        "GDP, 인구, 무역, 인플레이션 등 16,000+ 지표."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "country": {
                "type": "string",
                "description": "ISO 국가 코드 (예: KR, US, CN, JP)",
            },
            "indicator": {
                "type": "string",
                "description": (
                    "지표 코드. 주요 지표: "
                    "NY.GDP.MKTP.CD (GDP), SP.POP.TOTL (인구), "
                    "FP.CPI.TOTL.ZG (CPI), NE.EXP.GNFS.CD (수출)"
                ),
            },
            "start_year": {"type": "integer", "description": "시작 연도 (기본: 2018)"},
            "end_year": {"type": "integer", "description": "종료 연도 (기본: 2024)"},
        },
        "required": ["country", "indicator"],
    },
}


# ── Exchange Rate ──────────────────────────────────


async def exchange_rate(
    base: str = "USD",
    target: str | None = None,
) -> str:
    """Fetch current exchange rates."""
    url = f"https://open.er-api.com/v6/latest/{base}"
    resp = await _get_http().get(url)
    resp.raise_for_status()
    data = resp.json()

    if data.get("result") != "success":
        return json.dumps({"error": "API error", "detail": data}, ensure_ascii=False)

    if target:
        rate = data.get("rates", {}).get(target)
        if rate is None:
            return json.dumps({"error": f"Currency {target} not found"}, ensure_ascii=False)
        return json.dumps(
            {"base": base, "target": target, "rate": rate, "updated": data.get("time_last_update_utc")},
            ensure_ascii=False,
        )

    # Return top currencies if no target specified
    top_currencies = ["KRW", "USD", "EUR", "JPY", "CNY", "GBP"]
    rates = {k: v for k, v in data.get("rates", {}).items() if k in top_currencies}
    return json.dumps(
        {"base": base, "rates": rates, "updated": data.get("time_last_update_utc")},
        ensure_ascii=False,
    )


EXCHANGE_RATE_TOOL: dict[str, Any] = {
    "name": "exchange_rate",
    "description": (
        "환율 조회 — 160+ 통화 실시간 환율. "
        "target 미지정 시 주요 6개 통화(KRW, USD, EUR, JPY, CNY, GBP) 반환."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "base": {
                "type": "string",
                "description": "기준 통화 코드 (기본: USD)",
            },
            "target": {
                "type": "string",
                "description": "대상 통화 코드 (선택 — 미지정 시 주요 통화 반환)",
            },
        },
        "required": [],
    },
}


# ── IMF Data ───────────────────────────────────────


_ISO2_TO_ISO3: dict[str, str] = {
    "KR": "KOR", "US": "USA", "CN": "CHN", "JP": "JPN", "DE": "DEU",
    "GB": "GBR", "FR": "FRA", "IN": "IND", "BR": "BRA", "CA": "CAN",
    "AU": "AUS", "IT": "ITA", "ES": "ESP", "MX": "MEX", "ID": "IDN",
    "RU": "RUS", "SA": "SAU", "TR": "TUR", "NL": "NLD", "CH": "CHE",
    "SE": "SWE", "PL": "POL", "TH": "THA", "VN": "VNM", "SG": "SGP",
}


async def imf_data(
    country: str = "KR",
    indicator: str = "NGDPD",
    start_year: int = 2020,
    end_year: int = 2025,
) -> str:
    """Fetch IMF economic data via DataMapper API."""
    # IMF DataMapper uses ISO3 country codes
    iso3 = _ISO2_TO_ISO3.get(country.upper(), country.upper())
    url = (
        f"https://www.imf.org/external/datamapper/api/v1/{indicator}/{iso3}"
    )
    resp = await _get_http().get(url)
    resp.raise_for_status()
    data = resp.json()

    values = data.get("values", {}).get(indicator, {}).get(iso3, {})
    if not values:
        return json.dumps({"error": "No data found", "indicator": indicator, "country": iso3}, ensure_ascii=False)

    records = [
        {"year": yr, "value": val}
        for yr, val in sorted(values.items())
        if start_year <= int(yr) <= end_year
    ]

    indicator_label = data.get("meta", {}).get("label", {}).get(indicator, indicator)
    return json.dumps(
        {"indicator": indicator_label, "country": iso3, "data": records},
        ensure_ascii=False,
    )


IMF_DATA_TOOL: dict[str, Any] = {
    "name": "imf_data",
    "description": (
        "IMF 데이터 — 세계경제전망(WEO) 주요 지표 조회. "
        "GDP, 인플레이션, 실업률, 경상수지 등."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "country": {
                "type": "string",
                "description": "ISO 국가 코드 (예: KR, US, CN)",
            },
            "indicator": {
                "type": "string",
                "description": (
                    "WEO 지표 코드. 주요: NGDPD (GDP), PCPIPCH (CPI), "
                    "LUR (실업률), BCA (경상수지)"
                ),
            },
            "start_year": {"type": "integer", "description": "시작 연도 (기본: 2020)"},
            "end_year": {"type": "integer", "description": "종료 연도 (기본: 2025)"},
        },
        "required": ["country", "indicator"],
    },
}


# ── DBnomics ───────────────────────────────────────


async def dbnomics_data(
    provider: str,
    dataset: str,
    series: str,
    limit: int = 50,
) -> str:
    """Fetch economic data from DBnomics (ECB/OECD/Eurostat aggregator)."""
    url = (
        f"https://api.db.nomics.world/v22/series/{provider}/{dataset}/{series}"
        f"?observations=1&limit={limit}"
    )
    resp = await _get_http().get(url)
    resp.raise_for_status()
    data = resp.json()

    docs = data.get("series", {}).get("docs", [])
    if not docs:
        return json.dumps({"error": "No series found", "query": f"{provider}/{dataset}/{series}"}, ensure_ascii=False)

    result = []
    for doc in docs[:5]:
        period = doc.get("period", [])
        value = doc.get("value", [])
        pairs = [{"period": p, "value": v} for p, v in zip(period, value) if v is not None]
        result.append({
            "series_code": doc.get("series_code", ""),
            "series_name": doc.get("series_name", ""),
            "data": pairs[-limit:],
        })

    return json.dumps(result, ensure_ascii=False)


DBNOMICS_TOOL: dict[str, Any] = {
    "name": "dbnomics_data",
    "description": (
        "DBnomics — ECB/OECD/Eurostat 등 80+ 기관 경제 데이터 통합 조회. "
        "provider/dataset/series 경로로 시계열 데이터 검색."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "description": "데이터 제공기관 (예: ECB, OECD, Eurostat, BIS)",
            },
            "dataset": {
                "type": "string",
                "description": "데이터셋 ID (예: ECB의 EXR, OECD의 MEI)",
            },
            "series": {
                "type": "string",
                "description": "시리즈 ID (예: M.KRW.EUR.SP00.A)",
            },
            "limit": {
                "type": "integer",
                "description": "최대 데이터 포인트 수 (기본: 50)",
            },
        },
        "required": ["provider", "dataset", "series"],
    },
}


# ── pykrx (Korean Stock Market) ────────────────────


def _sync_pykrx_fetch(ticker: str, start: str, end: str, data_type: str) -> str:
    """Synchronous pykrx fetch — runs in thread via asyncio.to_thread()."""
    try:
        from pykrx import stock
    except ImportError:
        raise ToolError("pykrx not installed. Run: pip install pykrx")

    if data_type == "ohlcv":
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return json.dumps({"error": f"No OHLCV data for {ticker}"}, ensure_ascii=False)
        df.index = df.index.strftime("%Y-%m-%d")
        return df.tail(20).to_json(orient="index", force_ascii=False)

    elif data_type == "fundamental":
        df = stock.get_market_fundamental_by_date(start, end, ticker)
        if df.empty:
            return json.dumps({"error": f"No fundamental data for {ticker}"}, ensure_ascii=False)
        df.index = df.index.strftime("%Y-%m-%d")
        return df.tail(10).to_json(orient="index", force_ascii=False)

    elif data_type == "cap":
        df = stock.get_market_cap_by_date(start, end, ticker)
        if df.empty:
            return json.dumps({"error": f"No market cap data for {ticker}"}, ensure_ascii=False)
        df.index = df.index.strftime("%Y-%m-%d")
        return df.tail(10).to_json(orient="index", force_ascii=False)

    else:
        return json.dumps({"error": f"Unknown data_type: {data_type}. Use: ohlcv, fundamental, cap"})


async def pykrx_stock(
    ticker: str,
    start: str = "20240101",
    end: str = "20241231",
    data_type: str = "ohlcv",
) -> str:
    """Fetch Korean stock data via pykrx (KRX scraper)."""
    return await asyncio.to_thread(_sync_pykrx_fetch, ticker, start, end, data_type)


PYKRX_TOOL: dict[str, Any] = {
    "name": "pykrx_stock",
    "description": (
        "한국 주식 데이터 — KOSPI/KOSDAQ 주가, 시가총액, PER/PBR/배당수익률. "
        "한국 주식 데이터는 반드시 이 도구를 사용하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": "종목 코드 (예: 005930=삼성전자, 000660=SK하이닉스)",
            },
            "start": {
                "type": "string",
                "description": "시작일 YYYYMMDD (기본: 20240101)",
            },
            "end": {
                "type": "string",
                "description": "종료일 YYYYMMDD (기본: 20241231)",
            },
            "data_type": {
                "type": "string",
                "description": "데이터 종류: ohlcv(주가), fundamental(PER/PBR), cap(시가총액)",
                "enum": ["ohlcv", "fundamental", "cap"],
            },
        },
        "required": ["ticker"],
    },
}


# ── yfinance (Global Stocks) ──────────────────────


def _sync_yfinance_fetch(ticker: str, period: str, data_type: str) -> str:
    """Synchronous yfinance fetch — runs in thread via asyncio.to_thread()."""
    try:
        import yfinance as yf
    except ImportError:
        raise ToolError("yfinance not installed. Run: pip install yfinance")

    stock = yf.Ticker(ticker)

    if data_type == "price":
        df = stock.history(period=period)
        if df.empty:
            return json.dumps({"error": f"No price data for {ticker}"}, ensure_ascii=False)
        df.index = df.index.strftime("%Y-%m-%d")
        return df.tail(20).to_json(orient="index", force_ascii=False, date_format="iso")

    elif data_type == "financials":
        fs = stock.financials
        if fs is None or fs.empty:
            return json.dumps({"error": f"No financial statements for {ticker}"}, ensure_ascii=False)
        fs.columns = [c.strftime("%Y-%m-%d") if hasattr(c, "strftime") else str(c) for c in fs.columns]
        return fs.to_json(orient="columns", force_ascii=False)

    elif data_type == "info":
        info = stock.info
        keys = [
            "shortName", "sector", "industry", "marketCap", "trailingPE",
            "forwardPE", "dividendYield", "beta", "fiftyTwoWeekHigh",
            "fiftyTwoWeekLow", "currency", "exchange",
        ]
        filtered = {k: info.get(k) for k in keys if info.get(k) is not None}
        return json.dumps(filtered, ensure_ascii=False)

    else:
        return json.dumps({"error": f"Unknown data_type: {data_type}. Use: price, financials, info"})


async def yfinance_data(
    ticker: str,
    period: str = "1y",
    data_type: str = "price",
) -> str:
    """Fetch global stock data via yfinance."""
    return await asyncio.to_thread(_sync_yfinance_fetch, ticker, period, data_type)


YFINANCE_TOOL: dict[str, Any] = {
    "name": "yfinance_data",
    "description": (
        "글로벌 주식 데이터 — 미국/유럽/아시아 주가, 재무제표, 기업 정보. "
        "글로벌 주식은 반드시 이 도구를 사용하세요. 한국 주식은 pykrx_stock을 사용하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": "티커 심볼 (예: AAPL, MSFT, TSLA, 7203.T=토요타)",
            },
            "period": {
                "type": "string",
                "description": "조회 기간: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max (기본: 1y)",
            },
            "data_type": {
                "type": "string",
                "description": "데이터 종류: price(주가), financials(재무제표), info(기업정보)",
                "enum": ["price", "financials", "info"],
            },
        },
        "required": ["ticker"],
    },
}


# ── Fear & Greed Index ─────────────────────────────


async def fear_greed_index(limit: int = 7) -> str:
    """Fetch Crypto Fear & Greed Index."""
    url = f"https://api.alternative.me/fng/?limit={limit}&format=json"
    resp = await _get_http().get(url)
    resp.raise_for_status()
    data = resp.json()

    entries = data.get("data", [])
    records = [
        {
            "date": e.get("timestamp"),
            "value": int(e.get("value", 0)),
            "classification": e.get("value_classification", ""),
        }
        for e in entries
    ]
    return json.dumps(records, ensure_ascii=False)


FEAR_GREED_TOOL: dict[str, Any] = {
    "name": "fear_greed_index",
    "description": (
        "암호화폐 공포탐욕지수 — 일별 시장 심리 (0=극단적 공포, 100=극단적 탐욕)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "조회할 일수 (기본: 7, 최대 365)",
            },
        },
        "required": [],
    },
}


# ── DART OpenAPI (Korean corporate financial statements) ──


async def dart_financial(
    corp_code: str = "",
    corp_name: str = "",
    bsns_year: str = "2024",
    reprt_code: str = "11011",
) -> str:
    """Fetch Korean corporate financial statements from DART."""
    from src.config.settings import get_settings
    api_key = get_settings().dart_api_key
    if not api_key:
        raise ToolError("DART_API_KEY not configured")

    # If corp_name given, search for corp_code first
    if not corp_code and corp_name:
        search_url = f"https://opendart.fss.or.kr/api/company.json?crtfc_key={api_key}&corp_name={corp_name}"
        resp = await _get_http().get(search_url)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":
            return json.dumps({"error": f"Company not found: {corp_name}", "status": data.get("message")}, ensure_ascii=False)
        corp_code = data.get("corp_code", "")

    if not corp_code:
        return json.dumps({"error": "corp_code or corp_name required"}, ensure_ascii=False)

    # Fetch financial statements
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,  # 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기
        "fs_div": "CFS",  # 연결재무제표
    }
    resp = await _get_http().get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "000":
        return json.dumps({"error": data.get("message", "Unknown error"), "status": data.get("status")}, ensure_ascii=False)

    # Extract key items
    items = data.get("list", [])
    key_accounts = ["매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계"]
    filtered = [
        {
            "account": item.get("account_nm", ""),
            "current": item.get("thstrm_amount", ""),
            "previous": item.get("frmtrm_amount", ""),
        }
        for item in items
        if any(k in item.get("account_nm", "") for k in key_accounts)
    ]

    return json.dumps({
        "corp_code": corp_code,
        "year": bsns_year,
        "report_type": {"11011": "사업보고서", "11012": "반기", "11013": "1분기", "11014": "3분기"}.get(reprt_code, reprt_code),
        "financials": filtered if filtered else items[:20],
    }, ensure_ascii=False)


DART_FINANCIAL_TOOL: dict[str, Any] = {
    "name": "dart_financial",
    "description": (
        "DART 재무제표 — 한국 상장사 공식 재무데이터. "
        "매출액, 영업이익, 당기순이익, 자산/부채/자본 등. "
        "corp_code 또는 회사명으로 검색."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "corp_code": {
                "type": "string",
                "description": "DART 고유 기업코드 (8자리). 모르면 corp_name 사용",
            },
            "corp_name": {
                "type": "string",
                "description": "회사명 (예: 삼성전자, 카카오). corp_code 모를 때 사용",
            },
            "bsns_year": {
                "type": "string",
                "description": "사업연도 (기본: 2024)",
            },
            "reprt_code": {
                "type": "string",
                "description": "보고서 코드: 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기 (기본: 11011)",
            },
        },
        "required": [],
    },
}


# ── ECOS (Bank of Korea economic data) ───────────


async def ecos_data(
    stat_code: str,
    item_code: str = "0000001",
    cycle: str = "M",
    start_date: str = "202001",
    end_date: str = "202512",
) -> str:
    """Fetch Bank of Korea (ECOS) economic statistics."""
    from src.config.settings import get_settings
    api_key = get_settings().ecos_api_key
    if not api_key:
        raise ToolError("ECOS_API_KEY not configured")

    # ECOS REST API format: /api/StatisticSearch/{key}/{format}/{lang}/{start}/{end}/{code}/{cycle}/{start_date}/{end_date}/{item_code}
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/100"
        f"/{stat_code}/{cycle}/{start_date}/{end_date}/{item_code}"
    )
    resp = await _get_http().get(url)
    resp.raise_for_status()
    data = resp.json()

    result_data = data.get("StatisticSearch", {})
    if "row" not in result_data:
        error_msg = result_data.get("RESULT", {}).get("MESSAGE", "No data found")
        return json.dumps({"error": error_msg, "stat_code": stat_code}, ensure_ascii=False)

    records = [
        {
            "date": row.get("TIME", ""),
            "value": row.get("DATA_VALUE", ""),
            "stat_name": row.get("STAT_NAME", ""),
            "item_name": row.get("ITEM_NAME1", ""),
            "unit": row.get("UNIT_NAME", ""),
        }
        for row in result_data["row"]
    ]

    return json.dumps({"stat_code": stat_code, "data": records}, ensure_ascii=False)


ECOS_DATA_TOOL: dict[str, Any] = {
    "name": "ecos_data",
    "description": (
        "한국은행 경제통계 (ECOS) — 기준금리, 통화량, 물가지수, 국제수지 등 "
        "한국 중앙은행 공식 경제 데이터."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stat_code": {
                "type": "string",
                "description": (
                    "통계 코드. 주요: "
                    "722Y001 (기준금리), 901Y009 (M2 통화량), "
                    "021Y125 (소비자물가지수), 301Y013 (국제수지)"
                ),
            },
            "item_code": {
                "type": "string",
                "description": "항목 코드 (기본: 0000001). 통계별 세부 항목",
            },
            "cycle": {
                "type": "string",
                "description": "주기: M=월, Q=분기, A=연 (기본: M)",
                "enum": ["M", "Q", "A"],
            },
            "start_date": {
                "type": "string",
                "description": "시작일 (월: YYYYMM, 분기: YYYYQ1, 연: YYYY). 기본: 202001",
            },
            "end_date": {
                "type": "string",
                "description": "종료일. 기본: 202512",
            },
        },
        "required": ["stat_code"],
    },
}


# ── KOSIS (Korean Statistical Information Service) ─


async def kosis_data(
    stat_id: str,
    org_id: str = "101",
    start_period: str = "2020",
    end_period: str = "2025",
    item_id: str = "",
) -> str:
    """Fetch Korean government statistics from KOSIS."""
    from src.config.settings import get_settings
    api_key = get_settings().kosis_api_key
    if not api_key:
        raise ToolError("KOSIS_API_KEY not configured")

    url = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
    params = {
        "method": "getList",
        "apiKey": api_key,
        "itmId": item_id or "ALL",
        "objL1": "ALL",
        "objL2": "",
        "objL3": "",
        "objL4": "",
        "objL5": "",
        "objL6": "",
        "objL7": "",
        "objL8": "",
        "format": "json",
        "jsonVD": "Y",
        "prdSe": "Y",
        "startPrdDe": start_period,
        "endPrdDe": end_period,
        "orgId": org_id,
        "tblId": stat_id,
    }
    resp = await _get_http().get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict) and data.get("err"):
        return json.dumps({"error": data.get("errMsg", "Unknown error")}, ensure_ascii=False)

    if not isinstance(data, list) or not data:
        return json.dumps({"error": "No data found", "stat_id": stat_id}, ensure_ascii=False)

    records = [
        {
            "period": item.get("PRD_DE", ""),
            "category": item.get("C1_NM", ""),
            "subcategory": item.get("C2_NM", ""),
            "item": item.get("ITM_NM", ""),
            "value": item.get("DT", ""),
            "unit": item.get("UNIT_NM", ""),
        }
        for item in data[:50]
    ]

    return json.dumps({"stat_id": stat_id, "org_id": org_id, "data": records}, ensure_ascii=False)


KOSIS_DATA_TOOL: dict[str, Any] = {
    "name": "kosis_data",
    "description": (
        "KOSIS 통계 — 한국 정부 공식 통계 (고용, 임금, 인구, 경제활동 등). "
        "통계청 및 400+ 기관 데이터 조회."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stat_id": {
                "type": "string",
                "description": (
                    "통계표 ID. 주요: "
                    "DT_1DA7002S (경제활동인구), DT_1ES4F01 (임금), "
                    "DT_1IN1502 (인구추계), DT_1YL20631 (GDP)"
                ),
            },
            "org_id": {
                "type": "string",
                "description": "기관 ID (기본: 101=통계청)",
            },
            "start_period": {
                "type": "string",
                "description": "시작 기간 YYYY (기본: 2020)",
            },
            "end_period": {
                "type": "string",
                "description": "종료 기간 YYYY (기본: 2025)",
            },
        },
        "required": ["stat_id"],
    },
}
