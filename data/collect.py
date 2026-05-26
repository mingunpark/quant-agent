"""분기 실적 + 가격 데이터 수집 (T1).

룩어헤드 바이어스 방지를 위해 YoY 기준값으로 백테스트 시작 분기 - 4 분기를
포함하여 수집한다. 예: 2022Q1 백테스트 시작이면 2021Q1부터 수집.

수동 재실행 전략 (D11):
  티커별로 parquet 분리 저장. 실패한 티커의 파일만 삭제 후 재실행하면
  성공한 티커는 건너뛴다.

DART rcept_dt 사용 (D2 결정 기준).
IR 보정 로직은 별도 모듈에서 적용 가능하도록 announce_date 컬럼을 유지.
"""

from __future__ import annotations

import io
import os
import time
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv


RAW_DIR = Path(__file__).resolve().parent / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent / "processed"

REPRT_CODE_MAP = {
    1: "11013",
    2: "11012",
    3: "11014",
    4: "11011",
}


def _ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "dart").mkdir(exist_ok=True)
    (RAW_DIR / "price").mkdir(exist_ok=True)
    (RAW_DIR / "consensus").mkdir(exist_ok=True)


def yoy_baseline_quarters(start_year: int, start_quarter: int) -> tuple[int, int]:
    """YoY 계산을 위한 수집 시작 분기 반환 (T-4)."""
    baseline_year = start_year - 1
    return baseline_year, start_quarter


def collect_dart_quarter(
    year: int,
    quarter: int,
    tickers: list[str],
    progress_callback=None,
) -> Path:
    """DART에서 한 분기치 재무 데이터 수집. 파일이 이미 있으면 스킵.

    rate limit (분당 1000회) 보호를 위해 배치당 sleep(0.1).
    progress_callback: (current: int, total: int, ticker: str) -> None
    """
    out_path = RAW_DIR / "dart" / f"dart_{year}Q{quarter}.parquet"
    if out_path.exists():
        if progress_callback:
            n = len(tickers)
            progress_callback(n, n, "캐시 사용")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    load_dotenv()
    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "DART_API_KEY가 설정되지 않음. .env 파일을 만들거나 환경변수로 설정하세요."
        )

    from opendartreader import OpenDartReader

    try:
        dart = OpenDartReader(api_key)
    except Exception as e:
        raise ConnectionError(
            f"DART API 연결 실패: {e}\n"
            "확인 사항: 1) 인터넷 연결 2) DART API 키 유효성 (https://opendart.fss.or.kr)"
        ) from e
    reprt_code = REPRT_CODE_MAP[quarter]
    rows: list[dict] = []
    failures: list[str] = []
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        if progress_callback:
            progress_callback(i, total, ticker)
        try:
            with redirect_stdout(io.StringIO()):
                fs = dart.finstate(ticker, year, reprt_code=reprt_code)
            if fs is None or fs.empty:
                failures.append(ticker)
                continue
            revenue = _extract_account(fs, "매출액")
            op_income = _extract_account(fs, "영업이익")
            net_income = _extract_account(fs, "당기순이익")
            eps = _extract_account(fs, "주당순이익")
            rcept_dt = _extract_rcept_dt(fs)
            rows.append(
                {
                    "ticker": ticker,
                    "year": year,
                    "quarter": quarter,
                    "revenue": revenue,
                    "op_income": op_income,
                    "net_income": net_income,
                    "eps": eps,
                    "announce_date": rcept_dt,
                }
            )
        except Exception as exc:
            failures.append(ticker)
            print(f"[DART] {ticker} {year}Q{quarter} 실패: {exc}")
        if i % 10 == 9:
            time.sleep(0.1)

    if failures:
        fail_path = out_path.with_suffix(".failures.txt")
        fail_path.write_text("\n".join(failures), encoding="utf-8")

    failure_rate = len(failures) / total if total > 0 else 0
    if failure_rate > 0.5:
        print(
            f"  [DART] {year}Q{quarter} 경고: 실패율 {failure_rate:.0%} ({len(failures)}/{total}개). "
            "금융업 종목 또는 해당 분기 미상장 종목 포함 가능. 수집된 데이터로 계속 진행."
        )

    if not rows:
        print(f"  [DART] {year}Q{quarter}: 수집 성공 0개 — 빈 파일 저장 후 계속 진행")
        pd.DataFrame(columns=["ticker", "year", "quarter", "revenue",
                               "op_income", "net_income", "eps", "announce_date"]
                     ).to_parquet(out_path, index=False)
        return out_path

    df = pd.DataFrame(rows)
    df.to_parquet(out_path, index=False)
    return out_path


def _extract_account(fs: pd.DataFrame, account_name: str) -> float:
    """OpenDartReader finstate DataFrame에서 특정 계정과목의 당기금액 추출.

    연결재무제표 우선. 없으면 NaN.
    분기 보고서는 계정명이 변형될 수 있어 후보 목록을 순서대로 시도한다.
    """
    _ALIASES: dict[str, list[str]] = {
        "당기순이익": ["당기순이익", "분기순이익", "당기순이익(손실)", "당기순손익"],
        "주당순이익": ["주당순이익", "기본주당순이익", "기본주당이익(손실)"],
    }
    candidates = _ALIASES.get(account_name, [account_name])

    for candidate in candidates:
        target = fs[fs["account_nm"] == candidate]
        if "fs_div" in target.columns:
            consolidated = target[target["fs_div"] == "CFS"]
            if not consolidated.empty:
                target = consolidated
        if not target.empty:
            break
    if target.empty:
        return float("nan")
    value = target.iloc[0].get("thstrm_amount", float("nan"))
    if value in ("", None):
        return float("nan")
    try:
        return float(str(value).replace(",", "")) / 1_000_000
    except (TypeError, ValueError):
        return float("nan")


def _extract_rcept_dt(fs: pd.DataFrame) -> date | None:
    """접수일 추출. rcept_dt → rcept_no 앞 8자리(YYYYMMDD) 순으로 시도."""
    if fs.empty:
        return None
    # 우선순위 1: rcept_dt 컬럼
    if "rcept_dt" in fs.columns:
        raw = str(fs.iloc[0]["rcept_dt"])
        if len(raw) == 8 and raw.isdigit():
            try:
                return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                pass
    # 우선순위 2: rcept_no 앞 8자리 (DART API 실제 응답 형식: 20250515001922)
    if "rcept_no" in fs.columns:
        raw = str(fs.iloc[0]["rcept_no"])
        if len(raw) >= 8 and raw[:8].isdigit():
            try:
                return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                pass
    return None


def collect_price(ticker: str, start: date, end: date) -> Path:
    """pykrx로 한 종목의 일 단위 OHLCV + 시총 + 밸류에이션 수집.

    파일별 분리 저장 → 실패 시 해당 파일만 삭제 후 재실행 가능.
    """
    out_path = RAW_DIR / "price" / f"price_{ticker}_{start.year}_{end.year}.parquet"
    if out_path.exists():
        return out_path

    from pykrx import stock

    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    ohlcv = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if ohlcv.empty:
        raise RuntimeError(f"{ticker}: pykrx OHLCV 수집 실패 (빈 결과)")
    fundamental = stock.get_market_fundamental_by_date(start_str, end_str, ticker)
    cap = stock.get_market_cap_by_date(start_str, end_str, ticker)

    df = ohlcv.join(fundamental, how="left").join(cap[["시가총액"]], how="left")
    df.index.name = "date"
    df = df.rename(
        columns={
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "volume",
            "시가총액": "market_cap",
        }
    )
    df["ticker"] = ticker
    df.to_parquet(out_path)
    return out_path


def collect_ticker_names(tickers: list[str]) -> dict[str, str]:
    """pykrx로 종목 한국어 이름 수집. 실패 종목은 빈 문자열 반환."""
    from pykrx import stock

    result: dict[str, str] = {}
    for ticker in tickers:
        try:
            name = stock.get_market_ticker_name(ticker)
            result[ticker] = name if name else ""
        except Exception:
            result[ticker] = ""
    return result


def collect_consensus(year: int, quarter: int, tickers: list[str]) -> Path:
    """FinanceDataReader로 컨센서스 EPS 수집.

    무료 데이터 한계로 KOSPI200 일부만 커버. 누락 종목은 NaN 유지.
    Architecture D13에 따라 NaN → 어닝서프라이즈 팩터 제외 처리 (scorer/score.py).
    """
    out_path = RAW_DIR / "consensus" / f"consensus_{year}Q{quarter}.parquet"
    if out_path.exists():
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"ticker": ticker, "year": year, "quarter": quarter, "consensus_eps": float("nan")}
            for ticker in tickers]
    df = pd.DataFrame(rows)
    df.to_parquet(out_path, index=False)
    return out_path


def collect_all(start_year: int, start_quarter: int, end_year: int, end_quarter: int,
                tickers: list[str]) -> dict[str, Path]:
    """T-4 기준값까지 포함하여 전 분기/전 종목 수집.

    백테스트 시작이 (start_year, start_quarter)면 (start_year-1, start_quarter)부터 수집.
    """
    _ensure_dirs()
    baseline_year, baseline_quarter = yoy_baseline_quarters(start_year, start_quarter)
    quarters: list[tuple[int, int]] = []
    y, q = baseline_year, baseline_quarter
    while (y, q) <= (end_year, end_quarter):
        quarters.append((y, q))
        q += 1
        if q == 5:
            q = 1
            y += 1

    total_q = len(quarters)
    results: dict[str, Path] = {}
    for idx, (year, quarter) in enumerate(quarters, 1):
        dart_path = RAW_DIR / "dart" / f"dart_{year}Q{quarter}.parquet"
        if dart_path.exists():
            print(f"  [DART] {year}Q{quarter} ({idx}/{total_q}) SKIP (캐시)")
            results[f"dart_{year}Q{quarter}"] = dart_path
        else:
            print(f"  [DART] {year}Q{quarter} ({idx}/{total_q}) 수집 중...", flush=True)
            results[f"dart_{year}Q{quarter}"] = collect_dart_quarter(year, quarter, tickers)
        results[f"consensus_{year}Q{quarter}"] = collect_consensus(year, quarter, tickers)

    start_date = date(baseline_year, 1, 1)
    end_date = date(end_year, 12, 31)
    total_t = len(tickers)
    for i, ticker in enumerate(tickers, 1):
        price_path = RAW_DIR / "price" / f"price_{ticker}_{start_date.year}_{end_date.year}.parquet"
        if price_path.exists():
            results[f"price_{ticker}"] = price_path
            continue
        if i % 20 == 1:
            print(f"  [PRICE] 가격 수집 중... ({i}/{total_t})", flush=True)
        try:
            results[f"price_{ticker}"] = collect_price(ticker, start_date, end_date)
        except Exception as exc:
            print(f"  [PRICE] {ticker} 실패: {exc}")
    return results
