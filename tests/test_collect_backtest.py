"""scripts/collect_backtest_data.py 헬퍼 함수 단위 테스트 (D6).

검증 대상:
  - generate_quarters(): 경계값 (단일 분기, 연도 교차, 역방향 빈 결과)
  - subtract_quarters(): 연도 롤오버 (Q1-1 → 전년 Q4 등)
  - quarter_end_date(): 분기별 마지막 날
  - _assemble_price_matrix(): 파일 없음 / wide 행렬 형태
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.collect_backtest_data import (
    _assemble_price_matrix,
    generate_quarters,
    quarter_end_date,
    subtract_quarters,
)


# ---------------------------------------------------------------------------
# generate_quarters
# ---------------------------------------------------------------------------

class TestGenerateQuarters:
    def test_single_quarter(self):
        assert generate_quarters(2023, 1, 2023, 1) == [(2023, 1)]

    def test_same_year(self):
        result = generate_quarters(2023, 2, 2023, 4)
        assert result == [(2023, 2), (2023, 3), (2023, 4)]

    def test_cross_year_boundary(self):
        result = generate_quarters(2023, 3, 2024, 2)
        assert result == [(2023, 3), (2023, 4), (2024, 1), (2024, 2)]

    def test_full_twelve_quarters(self):
        result = generate_quarters(2023, 1, 2025, 4)
        assert len(result) == 12
        assert result[0] == (2023, 1)
        assert result[-1] == (2025, 4)

    def test_empty_when_start_after_end(self):
        assert generate_quarters(2024, 2, 2024, 1) == []

    def test_no_quarter_exceeds_4(self):
        result = generate_quarters(2022, 1, 2024, 4)
        quarters_only = [q for _, q in result]
        assert all(1 <= q <= 4 for q in quarters_only)

    def test_year_sequence_is_monotonic(self):
        result = generate_quarters(2023, 1, 2025, 4)
        years = [y for y, _ in result]
        assert years == sorted(years)


# ---------------------------------------------------------------------------
# subtract_quarters
# ---------------------------------------------------------------------------

class TestSubtractQuarters:
    def test_q1_minus_1_rolls_to_prev_q4(self):
        assert subtract_quarters(2023, 1, 1) == (2022, 4)

    def test_q1_minus_4_stays_same_quarter_prev_year(self):
        assert subtract_quarters(2023, 1, 4) == (2022, 1)

    def test_q1_minus_5_crosses_two_years(self):
        assert subtract_quarters(2023, 1, 5) == (2021, 4)

    def test_q2_minus_4_is_prev_year_q2(self):
        assert subtract_quarters(2024, 2, 4) == (2023, 2)

    def test_within_same_year(self):
        assert subtract_quarters(2024, 3, 2) == (2024, 1)

    def test_cross_year_mid_quarter(self):
        assert subtract_quarters(2024, 2, 3) == (2023, 3)

    def test_result_quarter_in_valid_range(self):
        for q in range(1, 5):
            for n in range(1, 8):
                _, rq = subtract_quarters(2024, q, n)
                assert 1 <= rq <= 4, f"subtract_quarters(2024, {q}, {n}) → quarter={rq}"


# ---------------------------------------------------------------------------
# quarter_end_date
# ---------------------------------------------------------------------------

class TestQuarterEndDate:
    def test_q1_ends_march_31(self):
        assert quarter_end_date(2024, 1) == date(2024, 3, 31)

    def test_q2_ends_june_30(self):
        assert quarter_end_date(2024, 2) == date(2024, 6, 30)

    def test_q3_ends_september_30(self):
        assert quarter_end_date(2024, 3) == date(2024, 9, 30)

    def test_q4_ends_december_31(self):
        assert quarter_end_date(2024, 4) == date(2024, 12, 31)

    def test_returns_date_type(self):
        d = quarter_end_date(2023, 2)
        assert isinstance(d, date)


# ---------------------------------------------------------------------------
# _assemble_price_matrix
# ---------------------------------------------------------------------------

class TestAssemblePriceMatrix:
    def test_empty_when_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.collect_backtest_data.RAW_PRICE_DIR", tmp_path
        )
        result = _assemble_price_matrix(["005930", "000660"])
        assert result.empty

    def test_wide_format_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.collect_backtest_data.RAW_PRICE_DIR", tmp_path
        )
        for ticker in ["005930", "000660"]:
            df = pd.DataFrame(
                {"close": [100.0, 101.0, 102.0]},
                index=pd.date_range("2024-01-02", periods=3, freq="B"),
            )
            df.to_parquet(tmp_path / f"price_{ticker}_2024_2024.parquet")

        result = _assemble_price_matrix(["005930", "000660"])
        assert set(result.columns) == {"005930", "000660"}
        assert len(result) == 3

    def test_missing_ticker_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.collect_backtest_data.RAW_PRICE_DIR", tmp_path
        )
        df = pd.DataFrame(
            {"close": [50.0, 51.0]},
            index=pd.date_range("2024-01-02", periods=2, freq="B"),
        )
        df.to_parquet(tmp_path / "price_005930_2024_2024.parquet")

        result = _assemble_price_matrix(["005930", "999999"])
        assert "005930" in result.columns
        assert "999999" not in result.columns
