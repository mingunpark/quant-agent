"""validator 단위 테스트.

verify_no_lookahead 통과 보장이 백테스트 신뢰성의 근본 게이트.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.validator import (
    FACTOR_INPUT_REQUIRED_COLUMNS,
    SIGNAL_INPUT_REQUIRED_COLUMNS,
    validate_schema,
    verify_no_lookahead,
)


def _make_signals(records: list[tuple[str, date, date]]) -> pd.DataFrame:
    return pd.DataFrame(
        records, columns=["ticker", "signal_date", "announce_date"]
    )


class TestVerifyNoLookahead:
    def test_all_signals_after_announce_returns_true(self):
        df = _make_signals([
            ("005930", date(2024, 5, 16), date(2024, 5, 14)),
            ("000660", date(2024, 5, 17), date(2024, 5, 15)),
        ])
        assert verify_no_lookahead(df) is True

    def test_single_violation_raises_value_error(self):
        df = _make_signals([
            ("005930", date(2024, 5, 16), date(2024, 5, 14)),
            ("000660", date(2024, 5, 10), date(2024, 5, 15)),
        ])
        with pytest.raises(ValueError, match="룩어헤드 바이어스 위반"):
            verify_no_lookahead(df)

    def test_boundary_equal_dates_is_violation(self):
        df = _make_signals([("005930", date(2024, 5, 14), date(2024, 5, 14))])
        with pytest.raises(ValueError, match="1건"):
            verify_no_lookahead(df)

    def test_empty_dataframe_returns_true(self):
        df = pd.DataFrame(columns=["ticker", "signal_date", "announce_date"])
        assert verify_no_lookahead(df) is True

    def test_missing_required_columns_raises_assertion(self):
        df = pd.DataFrame({"ticker": ["005930"], "signal_date": [date(2024, 5, 16)]})
        with pytest.raises(AssertionError, match="announce_date"):
            verify_no_lookahead(df)


class TestValidatePerformance:
    def test_all_pass(self):
        from backtest.validator import validate_performance
        result = validate_performance(
            cagr=0.15, benchmark_cagr=0.08, sharpe=0.7, mdd=-0.20
        )
        assert result["cagr_pass"] is True
        assert result["sharpe_pass"] is True
        assert result["mdd_pass"] is True

    def test_cagr_just_at_threshold_fails(self):
        from backtest.validator import validate_performance
        result = validate_performance(
            cagr=0.11, benchmark_cagr=0.08, sharpe=0.7, mdd=-0.20
        )
        assert result["cagr_pass"] is False  # 0.11 <= 0.08 + 0.03

    def test_sharpe_below_minimum_fails(self):
        from backtest.validator import validate_performance
        result = validate_performance(
            cagr=0.20, benchmark_cagr=0.08, sharpe=0.4, mdd=-0.20
        )
        assert result["sharpe_pass"] is False

    def test_mdd_exceeds_floor_fails(self):
        from backtest.validator import validate_performance
        result = validate_performance(
            cagr=0.20, benchmark_cagr=0.08, sharpe=0.7, mdd=-0.35
        )
        assert result["mdd_pass"] is False


class TestValidateSchema:
    def test_all_required_columns_present_returns_true(self, factor_input_basic):
        assert validate_schema(factor_input_basic, FACTOR_INPUT_REQUIRED_COLUMNS) is True

    def test_missing_column_raises_assertion(self, factor_input_basic):
        df = factor_input_basic.drop(columns=["is_valid"])
        with pytest.raises(AssertionError, match="is_valid"):
            validate_schema(df, FACTOR_INPUT_REQUIRED_COLUMNS)

    def test_signal_schema_validation(self):
        df = _make_signals([("005930", date(2024, 5, 16), date(2024, 5, 14))])
        assert validate_schema(df, SIGNAL_INPUT_REQUIRED_COLUMNS) is True
