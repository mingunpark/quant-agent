"""백테스트 실행 엔진 (T4 + T7).

핵심 결정사항:
  D14: 리밸런싱은 announce_date의 영업일 T+2에 진입
  D9: VectorBT freq='D' (일 단위 데이터), 리밸런싱은 신호로 제어
  D6 + T7: 결과 HTML/CSV에 factor_weights 스냅샷 자동 주입
  거래비용: 편도 0.015%, 슬리피지 0.1% (CLAUDE.md 공통 제약)

비용 산정: 편도 fee를 양방향에 적용하면 왕복 0.03% + 슬리피지 0.1% = 0.13%/회.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.validator import (
    SIGNAL_INPUT_REQUIRED_COLUMNS,
    validate_schema,
    validate_performance,
    verify_no_lookahead,
)
from scorer.score import CONFIG_PATH, load_weights

RESULTS_DIR = Path(__file__).resolve().parent / "results"

FEE = 0.00015
SLIPPAGE = 0.001
HOLDING_QUARTERS = 1
ENTRY_BUSINESS_DAYS_OFFSET = 2


def compute_signal_dates(top_n_results: dict[tuple[int, int], pd.DataFrame]) -> pd.DataFrame:
    """분기별 상위 N 결과 → 종목별 signal_date 시계열 생성.

    signal_date = announce_date + 2 영업일 (D14).
    공휴일은 KRX 거래일 기준이지만, pandas BusinessDay로 근사 (한국 공휴일은
    pandas-market-calendars로 확장 가능 — TODO로 위임).
    """
    rows = []
    for (year, quarter), top in top_n_results.items():
        if top.empty:
            continue
        for _, row in top.iterrows():
            announce = pd.to_datetime(row["announce_date"])
            if pd.isna(announce):
                continue
            signal_date = announce + pd.tseries.offsets.BDay(ENTRY_BUSINESS_DAYS_OFFSET)
            rows.append({
                "ticker": row["ticker"],
                "year": year,
                "quarter": quarter,
                "announce_date": announce,
                "signal_date": signal_date,
                "weight": 1.0 / len(top),
            })
    return pd.DataFrame(rows)


def build_signal_matrix(signals: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """signal_date를 entries/exits 불리언 행렬로 변환.

    HOLDING_QUARTERS 분기 후 청산. 다음 분기 신호 발생 종목은 재진입.
    """
    entries = pd.DataFrame(False, index=prices.index, columns=prices.columns)
    exits = pd.DataFrame(False, index=prices.index, columns=prices.columns)
    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        if ticker not in entries.columns:
            continue
        entry_date = pd.Timestamp(sig["signal_date"])
        exit_date = entry_date + pd.DateOffset(months=3 * HOLDING_QUARTERS)
        nearest_entry = entries.index[entries.index >= entry_date]
        nearest_exit = exits.index[exits.index >= exit_date]
        if len(nearest_entry) == 0:
            continue
        entries.at[nearest_entry[0], ticker] = True
        if len(nearest_exit) > 0:
            exits.at[nearest_exit[0], ticker] = True
    return entries, exits


def run_backtest(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark_returns: pd.Series | None = None,
    strategy_name: str = "factor_top20",
) -> dict:
    """VectorBT로 포트폴리오 백테스트 실행.

    prices: index=date, columns=ticker, values=close price (long format wide).
    benchmark_returns: KOSPI200 일별 수익률 시리즈.
    """
    validate_schema(signals, SIGNAL_INPUT_REQUIRED_COLUMNS)
    verify_no_lookahead(signals)

    import vectorbt as vbt

    entries, exits = build_signal_matrix(signals, prices)
    portfolio = vbt.Portfolio.from_signals(
        close=prices,
        entries=entries,
        exits=exits,
        fees=FEE,
        slippage=SLIPPAGE,
        freq="D",
        init_cash=100_000_000,
    )

    stats = portfolio.stats()
    annual_returns = portfolio.returns().resample("YE").apply(lambda r: (1 + r).prod() - 1)

    cagr = float(stats.get("Annualized Return [%]", np.nan)) / 100
    sharpe = float(stats.get("Sharpe Ratio", np.nan))
    mdd = float(stats.get("Max Drawdown [%]", np.nan)) / -100

    benchmark_cagr = 0.0
    if benchmark_returns is not None and len(benchmark_returns) > 0:
        years = (benchmark_returns.index[-1] - benchmark_returns.index[0]).days / 365.25
        if years > 0:
            benchmark_cagr = (1 + benchmark_returns).prod() ** (1 / years) - 1

    performance = validate_performance(cagr, benchmark_cagr, sharpe, mdd)

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    period_str = f"{prices.index[0].date()}_{prices.index[-1].date()}"
    artifacts = _write_artifacts(
        strategy_name=strategy_name,
        period=period_str,
        timestamp=timestamp,
        stats=stats,
        annual_returns=annual_returns,
        cagr=cagr,
        benchmark_cagr=benchmark_cagr,
        sharpe=sharpe,
        mdd=mdd,
        performance=performance,
        portfolio=portfolio,
        signals=signals,
    )

    return {
        "cagr": cagr,
        "benchmark_cagr": benchmark_cagr,
        "sharpe": sharpe,
        "mdd": mdd,
        "performance_pass": performance,
        "artifacts": artifacts,
    }


def _write_artifacts(
    strategy_name: str,
    period: str,
    timestamp: str,
    stats: pd.Series,
    annual_returns: pd.Series,
    cagr: float,
    benchmark_cagr: float,
    sharpe: float,
    mdd: float,
    performance: dict[str, bool],
    portfolio,
    signals: pd.DataFrame,
) -> dict[str, Path]:
    """결과 HTML/CSV에 factor_weights 스냅샷 자동 주입 (T7).

    quantstats가 설치되어 있으면 HTML 리포트 생성. 아니면 텍스트 요약만.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    weights = load_weights(CONFIG_PATH)
    weights_snapshot = json.dumps(weights, indent=2, ensure_ascii=False)

    annual_path = RESULTS_DIR / f"annual_breakdown_{strategy_name}_{timestamp}.csv"
    annual_df = annual_returns.to_frame(name="return")
    annual_df.to_csv(annual_path, encoding="utf-8")

    summary_path = RESULTS_DIR / f"summary_{strategy_name}_{period}_{timestamp}.txt"
    summary_path.write_text(
        _format_summary(
            strategy_name, period, cagr, benchmark_cagr, sharpe, mdd,
            performance, stats, weights_snapshot, len(signals),
        ),
        encoding="utf-8",
    )

    lookahead_path = RESULTS_DIR / f"lookahead_check_{strategy_name}_{timestamp}.log"
    lookahead_path.write_text(
        f"signals_checked: {len(signals)}\nviolations: 0\ntimestamp: {timestamp}\n",
        encoding="utf-8",
    )

    html_path = None
    try:
        import quantstats as qs
        html_path = RESULTS_DIR / f"summary_{strategy_name}_{period}_{timestamp}.html"
        returns = portfolio.returns()
        qs.reports.html(returns, output=str(html_path), title=f"{strategy_name} {period}")
        _inject_weights_into_html(html_path, weights_snapshot)
    except ImportError:
        pass
    except Exception as exc:
        (RESULTS_DIR / f"quantstats_error_{timestamp}.log").write_text(str(exc))

    return {
        "summary": summary_path,
        "annual": annual_path,
        "lookahead": lookahead_path,
        "html": html_path,
    }


def _format_summary(
    strategy_name: str,
    period: str,
    cagr: float,
    benchmark_cagr: float,
    sharpe: float,
    mdd: float,
    performance: dict[str, bool],
    stats: pd.Series,
    weights_snapshot: str,
    signal_count: int,
) -> str:
    return (
        f"Backtest Summary: {strategy_name}\n"
        f"Period: {period}\n"
        f"Signals: {signal_count}\n"
        f"\n"
        f"--- Performance ---\n"
        f"CAGR:               {cagr:.2%}\n"
        f"Benchmark CAGR:     {benchmark_cagr:.2%}\n"
        f"Excess CAGR:        {cagr - benchmark_cagr:.2%}\n"
        f"Sharpe Ratio:       {sharpe:.3f}\n"
        f"Max Drawdown:       {mdd:.2%}\n"
        f"\n"
        f"--- Pass/Fail ---\n"
        f"CAGR > BM + 3%:     {performance['cagr_pass']}\n"
        f"Sharpe >= 0.5:      {performance['sharpe_pass']}\n"
        f"MDD >= -30%:        {performance['mdd_pass']}\n"
        f"\n"
        f"--- Factor Weights Snapshot ---\n"
        f"{weights_snapshot}\n"
        f"\n"
        f"--- Disclaimer ---\n"
        f"과거 성과는 미래를 보장하지 않으며, 특히 2020년 이후 유동성 장세 영향을 고려해야 합니다.\n"
        f"DART 정정 공시 반영본을 사용하여 일부 데이터가 발표 당시와 다를 수 있음.\n"
        f"\n"
        f"--- Full Stats ---\n"
        f"{stats.to_string()}\n"
    )


def _inject_weights_into_html(html_path: Path, weights_snapshot: str) -> None:
    """quantstats HTML 리포트 끝에 가중치 스냅샷 섹션 추가."""
    if not html_path.exists():
        return
    content = html_path.read_text(encoding="utf-8")
    injection = (
        f"\n<hr><h2>Factor Weights Snapshot</h2>\n"
        f"<pre>{weights_snapshot}</pre>\n"
    )
    if "</body>" in content:
        content = content.replace("</body>", injection + "</body>")
    else:
        content += injection
    html_path.write_text(content, encoding="utf-8")
