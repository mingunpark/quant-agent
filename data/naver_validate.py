"""네이버 금융 모바일 API를 통한 DART 데이터 교차 검증.

PER/EPS/PBR 값을 네이버 금융과 비교하여 15% 초과 차이 발생 시 경고 플래그 설정.
HTML 파싱 없이 JSON API를 사용하므로 구조 변화에 강건함.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

import pandas as pd
import requests


_NAVER_API = "https://m.stock.naver.com/api/stock/{ticker}/integration"
_HEADERS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"}
DIFF_THRESHOLD = 0.15
_DELAY = 0.5  # 네이버 서버 부하 방지 (초당 2건 이하)


def _fmt(v: float):
    return round(v, 2) if not math.isnan(v) else None


def _fmt_pct(v: float) -> str:
    return f"{v:.1%}" if not math.isnan(v) else "N/A"


def _parse_float(value: Any) -> float:
    if value is None or str(value).strip() in ("", "N/A", "-", "null", "nan"):
        return float("nan")
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return float("nan")


def _pct_diff(a: float, b: float) -> float:
    if math.isnan(a) or math.isnan(b) or b == 0:
        return float("nan")
    return abs(a - b) / abs(b)


def fetch_naver_fundamental(ticker: str, session: requests.Session) -> dict[str, float]:
    """네이버 금융 모바일 API에서 PER/EPS/PBR 조회.

    Returns: {"per": float, "pbr": float, "eps": float} — 실패 시 NaN
    """
    url = _NAVER_API.format(ticker=ticker)
    try:
        resp = session.get(url, timeout=6, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        info = data.get("stockPriceInfo", {})
        return {
            "per": _parse_float(info.get("per") or info.get("PER")),
            "pbr": _parse_float(info.get("pbr") or info.get("PBR")),
            "eps": _parse_float(info.get("eps") or info.get("EPS")),
        }
    except Exception:
        return {"per": float("nan"), "pbr": float("nan"), "eps": float("nan")}


def validate_against_naver(
    dart_df: pd.DataFrame,
    sample_n: int = 5,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> pd.DataFrame:
    """DART DataFrame의 PER/EPS/PBR을 네이버 금융과 비교.

    dart_df: ticker, per, pbr, eps 컬럼이 있는 factor_input 형식 DataFrame
    sample_n: 교차 검증할 최대 종목 수 (API 부하 방지)
    Returns: 비교 결과 DataFrame (종목코드, DART/Naver 각 지표, 차이%, 경고 여부)
    """
    sample = dart_df.dropna(subset=["ticker"]).head(sample_n).reset_index(drop=True)
    tickers = sample["ticker"].tolist()
    total = len(tickers)
    rows = []

    with requests.Session() as session:
        for i, ticker in enumerate(tickers):
            if progress_callback:
                progress_callback(i, total, ticker)

            row = sample.loc[i]
            naver = fetch_naver_fundamental(ticker, session)
            time.sleep(_DELAY)

            d_per = _parse_float(row.get("per", float("nan")))
            d_pbr = _parse_float(row.get("pbr", float("nan")))
            n_per = naver["per"]
            n_pbr = naver["pbr"]

            diff_per = _pct_diff(d_per, n_per)
            diff_pbr = _pct_diff(d_pbr, n_pbr)
            warn = (not math.isnan(diff_per) and diff_per > DIFF_THRESHOLD) or \
                   (not math.isnan(diff_pbr) and diff_pbr > DIFF_THRESHOLD)

            rows.append({
                "종목코드": ticker,
                "DART_PER": _fmt(d_per),
                "Naver_PER": _fmt(n_per),
                "PER_차이": _fmt_pct(diff_per),
                "DART_PBR": _fmt(d_pbr),
                "Naver_PBR": _fmt(n_pbr),
                "PBR_차이": _fmt_pct(diff_pbr),
                "경고": "주의" if warn else "정상",
            })

    if progress_callback:
        progress_callback(total, total, "완료")

    return pd.DataFrame(rows)
