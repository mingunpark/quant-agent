"""raw 데이터 → scorer 입력 스키마 변환.

YoY 성장률, 영업이익률, 어닝 서프라이즈 계산. is_valid 필터.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd


RAW_DIR = Path(__file__).resolve().parent / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent / "processed"


def _previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, quarter)


def _load_dart_pair(year: int, quarter: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    current_path = RAW_DIR / "dart" / f"dart_{year}Q{quarter}.parquet"
    prev_year, prev_quarter = _previous_quarter(year, quarter)
    prev_path = RAW_DIR / "dart" / f"dart_{prev_year}Q{prev_quarter}.parquet"
    if not current_path.exists():
        raise FileNotFoundError(f"{current_path} 없음. data.collect 먼저 실행")
    if not prev_path.exists():
        raise FileNotFoundError(
            f"YoY 기준 분기 {prev_path} 없음. T-4 분기까지 수집 필요"
        )
    return pd.read_parquet(current_path), pd.read_parquet(prev_path)


def build_factor_input(
    year: int,
    quarter: int,
    universe_tickers: list[str],
    name_map: dict[str, str] | None = None,
) -> Path:
    """주어진 분기의 factor_input parquet 생성.

    universe_tickers: KOSPI200 또는 KOSDAQ150 구성 종목 코드 리스트.
    name_map: 종목코드 → 종목명 사전. None이면 내부에서 pykrx 조회.
              배치 실행 시 한 번만 조회 후 전달하면 분기별 중복 호출 방지 (D7).
    출력 스키마 (data/CLAUDE.md):
      ticker, name, announce_date, revenue, op_income, net_income, eps,
      eps_yoy, op_margin, revenue_yoy, pbr, per, eps_surprise, market_cap, is_valid
    """
    current, prev = _load_dart_pair(year, quarter)
    consensus_path = RAW_DIR / "consensus" / f"consensus_{year}Q{quarter}.parquet"
    consensus = pd.read_parquet(consensus_path) if consensus_path.exists() else pd.DataFrame(
        columns=["ticker", "consensus_eps"]
    )

    merged = current.merge(
        prev[["ticker", "eps", "revenue", "net_income"]].rename(
            columns={
                "eps": "eps_prev",
                "revenue": "revenue_prev",
                "net_income": "net_income_prev",
            }
        ),
        on="ticker",
        how="left",
    ).merge(
        consensus[["ticker", "consensus_eps"]],
        on="ticker",
        how="left",
    )

    merged["revenue_yoy"] = merged.apply(
        lambda r: _safe_yoy(r["revenue"], r["revenue_prev"]), axis=1
    )
    merged["op_margin"] = merged.apply(
        lambda r: _safe_div(r["op_income"], r["revenue"]), axis=1
    )
    merged["eps_surprise"] = merged.apply(
        lambda r: _safe_yoy(r["eps"], r["consensus_eps"]), axis=1
    )

    price_snapshot = _load_latest_valuation(merged["ticker"].tolist(), year, quarter)
    prev_price = _load_latest_valuation(merged["ticker"].tolist(), year - 1, quarter)
    prev_eps = prev_price[["ticker", "eps_pykrx"]].rename(columns={"eps_pykrx": "eps_pykrx_prev"})
    merged = merged.merge(price_snapshot, on="ticker", how="left")
    merged = merged.merge(prev_eps, on="ticker", how="left")

    # eps_yoy: price merge 후 계산 (pykrx EPS fallback이 eps_pykrx_prev를 사용)
    merged["eps_yoy"] = merged.apply(lambda r: _eps_yoy_with_fallback(r), axis=1)

    if name_map is None:
        from data.collect import collect_ticker_names
        name_map = collect_ticker_names(merged["ticker"].tolist())
    merged["name"] = merged["ticker"].map(name_map).fillna("")

    merged["is_valid"] = merged["ticker"].isin(universe_tickers)

    output_cols = [
        "ticker", "name", "announce_date",
        "revenue", "op_income", "net_income", "eps",
        "eps_yoy", "op_margin", "revenue_yoy",
        "pbr", "per", "eps_surprise", "market_cap", "is_valid",
    ]
    out = merged.reindex(columns=output_cols)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"factor_input_{year}Q{quarter}.parquet"
    out.to_parquet(out_path, index=False)
    return out_path


def _eps_yoy_with_fallback(row: pd.Series) -> float:
    """EPS YoY 계산. 우선순위: DART EPS → DART 순이익 → pykrx TTM EPS.

    DART 분기 보고서(11013/11012/11014)는 주당순이익을 미제공하는 경우가 많아
    순이익 YoY, 그다음 pykrx TTM EPS YoY를 대리 지표로 사용한다.
    pykrx EPS는 직전 4분기 합산(TTM) 기준이므로 정확한 분기 EPS와 다를 수 있음.
    """
    yoy = _safe_yoy(row["eps"], row["eps_prev"])
    if not math.isnan(yoy):
        return yoy
    yoy = _safe_yoy(row["net_income"], row["net_income_prev"])
    if not math.isnan(yoy):
        return yoy
    return _safe_yoy(
        row.get("eps_pykrx", float("nan")),
        row.get("eps_pykrx_prev", float("nan")),
    )


def _safe_yoy(current: float, previous: float) -> float:
    """YoY = (현재 - 이전) / |이전|. 이전이 NaN이거나 0이면 NaN (Architecture D5)."""
    if previous is None or current is None:
        return float("nan")
    try:
        prev_f = float(previous)
        curr_f = float(current)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(prev_f) or math.isnan(curr_f) or prev_f == 0:
        return float("nan")
    return (curr_f - prev_f) / abs(prev_f)


def _safe_div(numerator: float, denominator: float) -> float:
    if numerator is None or denominator is None:
        return float("nan")
    try:
        n, d = float(numerator), float(denominator)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(n) or math.isnan(d) or d == 0:
        return float("nan")
    return n / d


def _load_latest_valuation(tickers: list[str], year: int, quarter: int) -> pd.DataFrame:
    """분기 마지막 영업일의 PBR/PER/EPS/시가총액 스냅샷 추출.

    parquet 읽기 실패 시 최대 3회 재시도. 재시도 후에도 실패하면 NaN 행으로 처리.
    """
    _nan_row = lambda t: {"ticker": t, "pbr": float("nan"), "per": float("nan"),
                          "market_cap": float("nan"), "eps_pykrx": float("nan")}
    rows = []
    quarter_end = pd.Timestamp(year, 3 * quarter, 1) + pd.offsets.QuarterEnd(0)
    for ticker in tickers:
        candidates = list((RAW_DIR / "price").glob(f"price_{ticker}_*.parquet"))
        if not candidates:
            rows.append(_nan_row(ticker))
            continue
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                df = pd.read_parquet(candidates[0])
                df = df[df.index <= quarter_end]
                if df.empty:
                    rows.append(_nan_row(ticker))
                else:
                    last = df.iloc[-1]
                    rows.append({
                        "ticker": ticker,
                        "pbr": float(last.get("PBR", float("nan"))),
                        "per": float(last.get("PER", float("nan"))),
                        "market_cap": float(last.get("market_cap", float("nan"))),
                        "eps_pykrx": float(last.get("EPS", float("nan"))),
                    })
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            print(f"[VALUATION] {ticker} 읽기 실패 (3회 시도): {last_exc}")
            rows.append(_nan_row(ticker))
    return pd.DataFrame(rows)
