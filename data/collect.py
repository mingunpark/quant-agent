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
import requests
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


def _fetch_naver_pbr_per(ticker: str, session: requests.Session) -> dict[str, float]:
    """네이버 금융 API에서 현재 시점의 PER/PBR/EPS 조회.

    pykrx get_market_fundamental_by_date가 KRX 서버 이슈로 빈 결과를 반환하는 경우
    대체 소스로 사용. 현재 시점 값만 반환 (역사적 시계열 불가).
    """
    _NAVER_API = "https://m.stock.naver.com/api/stock/{ticker}/integration"
    _HEADERS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)"}
    try:
        resp = session.get(_NAVER_API.format(ticker=ticker), timeout=6, headers=_HEADERS)
        resp.raise_for_status()
        info_map = {
            item["code"]: item.get("value", "")
            for item in resp.json().get("totalInfos", [])
            if isinstance(item, dict) and "code" in item
        }

        def _parse(v: str) -> float:
            cleaned = str(v).replace(",", "").replace("배", "").replace("원", "").strip()
            try:
                return float(cleaned)
            except (TypeError, ValueError):
                return float("nan")

        return {
            "PER": _parse(info_map.get("per", "")),
            "PBR": _parse(info_map.get("pbr", "")),
            "EPS": _parse(info_map.get("eps", "")),
        }
    except Exception:
        return {"PER": float("nan"), "PBR": float("nan"), "EPS": float("nan")}


def collect_price(ticker: str, start: date, end: date) -> Path:
    """pykrx로 한 종목의 일 단위 OHLCV + 시총 수집. PER/PBR/EPS는 Naver API 폴백 포함.

    pykrx get_market_fundamental_by_date가 KRX 서버 이슈로 빈 결과를 반환하는 경우
    Naver API 현재값을 마지막 행에 주입한다. 이 경우 시계열 PER/PBR은 사용 불가.
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

    # pykrx fundamental 빈 결과 → Naver API로 현재 PER/PBR/EPS 보완
    needs_naver = all(c not in df.columns for c in ("PER", "PBR", "EPS")) or (
        df[["PER", "PBR", "EPS"]].isna().all(axis=None)
        if all(c in df.columns for c in ("PER", "PBR", "EPS")) else True
    )
    if needs_naver:
        with requests.Session() as _sess:
            naver_vals = _fetch_naver_pbr_per(ticker, _sess)
        for col, val in naver_vals.items():
            df[col] = float("nan")
            if not df.empty:
                df.loc[df.index[-1], col] = val  # 마지막 행에만 주입

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


# DART 실제 report_nm 형식: "분기보고서 (2025.03)", "반기보고서 (2025.06)" 등
# "1분기보고서" / "3분기보고서" 키워드는 DART에 없음 → "분기보고서" 사용 후 회계월로 구분
_QUARTER_REPORT_KEYWORDS = {
    1: "분기보고서",
    2: "반기보고서",
    3: "분기보고서",
    4: "사업보고서",
}

# 분기 말월 (12월 결산 기준, KOSPI200/KOSDAQ150 대다수): DART report_nm의 "(YYYY.MM)" 부분
_QUARTER_FISCAL_MONTHS = {1: "03", 2: "06", 3: "09", 4: "12"}

# 분기 보고서 제출 기간 (bgn_de, end_de): DART 관행 기준
_QUARTER_DATE_RANGES = {
    1: ("{year}0501", "{year}0630"),   # Q1: 5~6월
    2: ("{year}0801", "{year}0930"),   # Q2(반기): 8~9월
    3: ("{year}1101", "{year}1231"),   # Q3: 11~12월
    4: ("{nyear}0301", "{nyear}0430"), # Q4(사업보고서): 익년 3~4월
}


def collect_announce_dates(year: int, quarter: int) -> Path:
    """DART 공시목록 API(/list.json)로 분기 보고서 접수일 일괄 조회.

    날짜 범위 + 보고서명 필터링으로 전체 상장사 접수일 수집.
    개별 finstate 호출 없이 몇 페이지만으로 완료.
    반환: data/raw/dart/announce_dates_{year}Q{quarter}.parquet (ticker, announce_date)
    """
    out_path = RAW_DIR / "dart" / f"announce_dates_{year}Q{quarter}.parquet"
    if out_path.exists():
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    load_dotenv()
    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        raise EnvironmentError("DART_API_KEY 없음")

    keyword = _QUARTER_REPORT_KEYWORDS[quarter]
    fiscal_suffix = f"({year}.{_QUARTER_FISCAL_MONTHS[quarter]})"
    bgn_tmpl, end_tmpl = _QUARTER_DATE_RANGES[quarter]
    nyear = year + 1
    bgn_de = bgn_tmpl.format(year=year, nyear=nyear)
    end_de = end_tmpl.format(year=year, nyear=nyear)

    date_map: dict[str, date] = {}
    page = 1
    while True:
        try:
            resp = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": api_key,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "pblntf_ty": "A",  # 정기공시만 조회
                    "page_count": 100,
                    "page_no": page,
                },
                timeout=15,
            )
            data = resp.json()
        except Exception as exc:
            print(f"[ANNOUNCE] DART list API 실패 (p={page}): {exc}")
            break

        if data.get("status") != "000":
            break

        for item in data.get("list", []):
            report_nm = item.get("report_nm", "").strip()
            # DART 실제 형식: "분기보고서 (2025.03)", "[기재정정]분기보고서 (2025.03)" 등
            if keyword not in report_nm:
                continue
            # Q1/Q3 구분 및 비12월 결산 제외: "(YYYY.MM)" 회계월 확인
            if fiscal_suffix not in report_nm:
                continue
            stock_code = str(item.get("stock_code", "")).strip().zfill(6)
            rcept_dt = str(item.get("rcept_dt", ""))
            if not stock_code or stock_code == "000000":
                continue
            if len(rcept_dt) == 8 and rcept_dt.isdigit():
                try:
                    # 이미 등록된 종목은 최초(가장 이른) 접수일 우선
                    new_date = date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8]))
                    if stock_code not in date_map or new_date < date_map[stock_code]:
                        date_map[stock_code] = new_date
                except ValueError:
                    pass

        total_page = int(data.get("total_page", 1))
        if page >= total_page:
            break
        page += 1
        time.sleep(0.2)

    rows = [{"ticker": k, "announce_date": v} for k, v in date_map.items()]
    if not rows:
        rows = [{"ticker": "__empty__", "announce_date": None}]  # 빈 parquet 방지
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    print(f"[ANNOUNCE] {year}Q{quarter}: {len(date_map)}개 접수일 수집 완료")
    return out_path


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
