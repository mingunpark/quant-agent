"""백테스트 사전 검증 (룩어헤드 바이어스 + 스키마).

verify_no_lookahead()는 backtest-validator SKILL의 필수 게이트.
위반 1건이라도 발견 시 ValueError로 중단한다.
"""

from __future__ import annotations

import pandas as pd


SIGNAL_INPUT_REQUIRED_COLUMNS = ("ticker", "signal_date", "announce_date")

FACTOR_INPUT_REQUIRED_COLUMNS = (
    "ticker", "announce_date", "revenue", "op_income", "eps",
    "eps_yoy", "op_margin", "revenue_yoy", "pbr", "eps_surprise", "is_valid",
)


def verify_no_lookahead(signals_df: pd.DataFrame) -> bool:
    """signal_date가 announce_date 이후(>)인지 검증.

    빈 DataFrame은 True (위반 0건).
    signal_date == announce_date는 위반 (>=가 아닌 > 사용).
    """
    if signals_df.empty:
        return True
    missing = [c for c in SIGNAL_INPUT_REQUIRED_COLUMNS if c not in signals_df.columns]
    if missing:
        raise AssertionError(f"signals_df 필수 컬럼 누락: {missing}")
    signal = pd.to_datetime(signals_df["signal_date"])
    announce = pd.to_datetime(signals_df["announce_date"])
    violations = signals_df[signal <= announce]
    if len(violations) > 0:
        sample = violations.head(3).to_dict(orient="records")
        raise ValueError(
            f"룩어헤드 바이어스 위반: {len(violations)}건. 예시: {sample}"
        )
    return True


def validate_schema(df: pd.DataFrame, required_columns: tuple[str, ...]) -> bool:
    """필수 컬럼 존재 여부 검증. 누락 시 AssertionError.

    재사용 가능하도록 required_columns를 파라미터로 받음 (factor_input / signal 양쪽).
    """
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise AssertionError(f"필수 컬럼 누락: {missing}")
    return True


def validate_performance(
    cagr: float,
    benchmark_cagr: float,
    sharpe: float,
    mdd: float,
    min_alpha: float = 0.03,
    min_sharpe: float = 0.5,
    max_drawdown_floor: float = -0.30,
) -> dict[str, bool]:
    """backtest-validator SKILL의 통과 기준 검증.

    mdd는 음수로 입력 (예: -0.25 = -25%). max_drawdown_floor와 비교.
    """
    return {
        "cagr_pass": cagr > benchmark_cagr + min_alpha,
        "sharpe_pass": sharpe >= min_sharpe,
        "mdd_pass": mdd >= max_drawdown_floor,
    }
