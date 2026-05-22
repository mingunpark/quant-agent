"""백테스트용 히스토리 데이터 수집 + 분기별 스코어링 배치 스크립트.

사용법:
  python scripts/collect_backtest_data.py
  python scripts/collect_backtest_data.py --force          # 기존 스코어 parquet 덮어쓰기
  python scripts/collect_backtest_data.py --skip-backtest  # 스코어링까지만 실행

실행 전 체크리스트:
  1. data/raw/kospi200_history/ 에 YYYYMMDD.csv 파일 배치 (build_universe_history 선행)
  2. .env 에 DART_API_KEY 설정
  3. config/backtest_quarters.json 범위 확인
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

# 프로젝트 루트를 sys.path에 추가 (scripts/ 하위에서 실행 시)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from data.collect import collect_all, collect_ticker_names
from data.process import build_factor_input
from data.universe import build_universe_history, get_universe_for_quarter
from scorer.score import run_for_quarter
from backtest.run import compute_signal_dates, run_backtest


CONFIG_PATH = _PROJECT_ROOT / "config" / "backtest_quarters.json"
RAW_PRICE_DIR = _PROJECT_ROOT / "data" / "raw" / "price"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def generate_quarters(
    start_year: int, start_quarter: int,
    end_year: int, end_quarter: int,
) -> list[tuple[int, int]]:
    """시작~종료 분기 목록 생성 (양 끝 포함)."""
    quarters: list[tuple[int, int]] = []
    y, q = start_year, start_quarter
    while (y, q) <= (end_year, end_quarter):
        quarters.append((y, q))
        q += 1
        if q == 5:
            q = 1
            y += 1
    return quarters


def quarter_end_date(year: int, quarter: int) -> date:
    """분기 마지막 날 반환."""
    last_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    month, day = last_day[quarter]
    return date(year, month, day)


def subtract_quarters(year: int, quarter: int, n: int) -> tuple[int, int]:
    """(year, quarter)에서 n개 분기 이전 반환. 연도 롤오버 처리."""
    total = (year - 1) * 4 + (quarter - 1) - n
    return total // 4 + 1, total % 4 + 1


def _assemble_price_matrix(tickers: list[str]) -> pd.DataFrame:
    """per-ticker price parquet → wide close-price DataFrame.

    반환: index=date(DatetimeIndex), columns=ticker, values=종가.
    수집된 파일이 없는 종목은 제외.
    """
    frames: dict[str, pd.Series] = {}
    for ticker in tickers:
        candidates = sorted(RAW_PRICE_DIR.glob(f"price_{ticker}_*.parquet"))
        if not candidates:
            continue
        try:
            df = pd.read_parquet(candidates[0])
            if "close" in df.columns:
                frames[ticker] = df["close"]
        except Exception as exc:
            print(f"  [PRICE] {ticker} 로드 실패: {exc}")
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="백테스트 히스토리 수집 + 스코어링 배치"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="기존 스코어 parquet이 있어도 스코어링 재실행"
    )
    parser.add_argument(
        "--skip-backtest", action="store_true",
        help="스코어링까지만 실행. 백테스트 엔진 생략"
    )
    args = parser.parse_args()

    cfg = load_config()
    start_year    = cfg["backtest_start"]["year"]
    start_quarter = cfg["backtest_start"]["quarter"]
    end_year      = cfg["backtest_end"]["year"]
    end_quarter   = cfg["backtest_end"]["quarter"]
    top_n         = cfg.get("scorer_top_n", 10)
    output_dir    = _PROJECT_ROOT / cfg.get("output_dir", "scorer/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    scoring_quarters = generate_quarters(start_year, start_quarter, end_year, end_quarter)
    print(
        f"백테스트 범위: {start_year}Q{start_quarter} ~ {end_year}Q{end_quarter}"
        f" ({len(scoring_quarters)}개 분기)"
    )

    # Step 0: KOSPI200 이력 parquet 빌드 (항상 최신 CSV 반영)
    print("\n[0/6] KOSPI200 이력 빌드...")
    build_universe_history("kospi200")

    # Step 1: 분기별 PIT 유니버스 + 전체 ticker union 산출
    print("[1/6] 분기별 유니버스 조회...")
    quarter_universes: dict[tuple[int, int], list[str]] = {}
    all_tickers: set[str] = set()
    for year, quarter in scoring_quarters:
        snap = quarter_end_date(year, quarter)
        tickers = get_universe_for_quarter(snap, index_name="kospi200")
        quarter_universes[(year, quarter)] = tickers
        all_tickers.update(tickers)
    all_tickers_list = sorted(all_tickers)
    print(f"   전체 유니버스 union: {len(all_tickers_list)}종목")

    # Step 2: DART + 가격 데이터 수집 (내부에서 YoY T-4 분기 자동 포함)
    print("\n[2/6] DART + 가격 수집 (최초 실행 시 약 30분, 이후 재실행은 skip)...")
    collect_all(start_year, start_quarter, end_year, end_quarter, all_tickers_list)

    # Step 3: 종목명 1회 조회 (D7 — 분기별 중복 호출 방지)
    print("[3/6] 종목명 조회 (1회)...")
    name_map = collect_ticker_names(all_tickers_list)

    # Step 4: 분기별 팩터 스코어링
    print("\n[4/6] 분기별 팩터 스코어링...")
    top_n_results: dict[tuple[int, int], pd.DataFrame] = {}

    for year, quarter in scoring_quarters:
        out_path = output_dir / f"top{top_n}_{year}Q{quarter}.parquet"

        if out_path.exists() and not args.force:
            print(f"   [{year}Q{quarter}] SKIP (--force로 재실행)")
            result = pd.read_parquet(out_path)
            top_n_results[(year, quarter)] = result
            continue

        universe_tickers = quarter_universes[(year, quarter)]
        try:
            factor_path = build_factor_input(
                year, quarter, universe_tickers, name_map=name_map
            )
            score_path = run_for_quarter(factor_path, year, quarter)

            result = pd.read_parquet(score_path)
            result["year"] = year
            result["quarter"] = quarter
            result.to_parquet(out_path, index=False)

            top_n_results[(year, quarter)] = result
            print(f"   [{year}Q{quarter}] 완료: {len(result)}종목 선별")
        except Exception as exc:
            print(f"   [{year}Q{quarter}] 오류: {exc}")

    scored = len(top_n_results)
    print(f"\n   스코어링 완료: {scored}/{len(scoring_quarters)}분기")

    if args.skip_backtest:
        print("\n--skip-backtest 지정. 백테스트 생략.")
        return

    if scored == 0:
        print("선별된 분기가 없어 백테스트를 건너뜁니다.")
        return

    # Step 5: 가격 wide 행렬 조립
    print("\n[5/6] 가격 행렬 조립...")
    prices = _assemble_price_matrix(all_tickers_list)
    if prices.empty:
        print(
            "가격 데이터를 불러올 수 없습니다. data/raw/price/ 디렉토리를 확인하세요."
        )
        return
    print(f"   가격 행렬: {len(prices)}일 × {len(prices.columns)}종목")

    # Step 6: 백테스트 실행
    print("\n[6/6] 백테스트 실행 중...")
    signals = compute_signal_dates(top_n_results)
    if signals.empty:
        print("생성된 신호가 없습니다. 스코어링 결과를 확인하세요.")
        return

    result = run_backtest(
        signals=signals,
        prices=prices,
        benchmark_returns=None,  # KOSPI200 벤치마크는 TODO-06 이후 추가
        strategy_name="factor_kospi200",
    )

    print("\n=== 백테스트 결과 ===")
    print(f"CAGR:      {result['cagr']:.2%}")
    print(f"Sharpe:    {result['sharpe']:.3f}")
    print(f"MDD:       {result['mdd']:.2%}")
    _pass = result["performance_pass"]
    print(f"CAGR 통과:  {_pass.get('cagr_pass', '?')}")
    print(f"Sharpe 통과: {_pass.get('sharpe_pass', '?')}")
    print(f"MDD 통과:   {_pass.get('mdd_pass', '?')}")
    print("\n결과 파일:")
    for k, path in result["artifacts"].items():
        if path:
            print(f"  {k}: {path}")


if __name__ == "__main__":
    main()
