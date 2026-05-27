"""pykrx / DART 데이터와 네이버 금융의 교차 검증.

PBR: pykrx(KRX BPS 기준) vs 네이버 — 15% 초과 차이 시 경고.
PER: 참고 표시만. pykrx TTM EPS vs 네이버 FY Annual EPS 방법론이 달라
     분기 실적 편차가 큰 종목은 50% 이상 차이가 날 수 있음 (데이터 오류 아님).
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
PBR_DIFF_THRESHOLD = 0.15   # PBR 15% 초과 시 경고 (같은 BPS 기준 비교 가능)
PER_DIFF_THRESHOLD = 0.50   # PER 참고용: TTM vs Annual EPS 방법론 차이로 50%+ 차이 가능
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


def _parse_naver_value(value: str) -> float:
    """네이버 금융 값 파싱. 한국어 단위 제거 후 float 변환.

    예시: "24.37배" → 24.37 / "12,372원" → 12372.0 / "N/A" → NaN
    """
    if not value or str(value).strip() in ("", "-", "N/A", "null"):
        return float("nan")
    cleaned = (
        str(value)
        .replace(",", "")
        .replace("배", "")
        .replace("원", "")
        .replace("%", "")
        .strip()
    )
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return float("nan")


def fetch_naver_fundamental(ticker: str, session: requests.Session) -> dict[str, float]:
    """네이버 금융 모바일 API에서 PER/EPS/PBR 조회.

    응답 구조: data["totalInfos"] = [{"code": "per", "value": "24.37배"}, ...]
    Returns: {"per": float, "pbr": float, "eps": float} — 실패 시 NaN
    """
    url = _NAVER_API.format(ticker=ticker)
    try:
        resp = session.get(url, timeout=6, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()

        # totalInfos: [{"code": "per", "key": "PER", "value": "24.37배"}, ...]
        total_infos = data.get("totalInfos", [])
        info_map: dict[str, str] = {}
        if isinstance(total_infos, list):
            info_map = {
                item["code"]: item.get("value", "")
                for item in total_infos
                if isinstance(item, dict) and "code" in item
            }

        return {
            "per": _parse_naver_value(info_map.get("per", "")),
            "pbr": _parse_naver_value(info_map.get("pbr", "")),
            "eps": _parse_naver_value(info_map.get("eps", "")),
        }
    except Exception:
        return {"per": float("nan"), "pbr": float("nan"), "eps": float("nan")}




def validate_against_naver(
    dart_df: pd.DataFrame,
    sample_n: int = 5,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> pd.DataFrame:
    """pykrx로 수집된 PER/PBR을 네이버 금융과 교차 검증.

    dart_df: ticker, per, pbr 컬럼이 있는 factor_input 형식 DataFrame
    sample_n: 교차 검증할 최대 종목 수 (API 부하 방지)
    per/pbr이 NaN인 경우 "미수집" 상태로 표시 — 가격 수집 후 재실행 필요.
    Returns: 비교 결과 DataFrame (종목코드, pykrx_PER/PBR, Naver_PER/PBR, 차이%, 경고)
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

            price_missing = math.isnan(d_per) and math.isnan(d_pbr)

            diff_per = _pct_diff(d_per, n_per)
            diff_pbr = _pct_diff(d_pbr, n_pbr)
            # 경고는 PBR만 기준: PER은 TTM vs Annual EPS 방법론 차이로 항상 크게 벌어짐
            pbr_warn = not math.isnan(diff_pbr) and diff_pbr > PBR_DIFF_THRESHOLD
            per_note = (
                "N/A" if math.isnan(d_per)
                else f"{_fmt_pct(diff_per)} ※TTM/Annual차이"
                if not math.isnan(diff_per) and diff_per > PER_DIFF_THRESHOLD
                else _fmt_pct(diff_per)
            )

            rows.append({
                "종목코드": ticker,
                "pykrx_PER": "미수집" if math.isnan(d_per) else _fmt(d_per),
                "Naver_PER": _fmt(n_per),
                "PER_차이": "N/A" if price_missing else per_note,
                "pykrx_PBR": "미수집" if math.isnan(d_pbr) else _fmt(d_pbr),
                "Naver_PBR": _fmt(n_pbr),
                "PBR_차이": "N/A" if math.isnan(d_pbr) else _fmt_pct(diff_pbr),
                "경고": "미수집" if price_missing else ("PBR주의" if pbr_warn else "정상"),
            })

    if progress_callback:
        progress_callback(total, total, "완료")

    return pd.DataFrame(rows)
