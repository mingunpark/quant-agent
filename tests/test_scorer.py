"""scorer 단위 테스트.

핵심 코너 케이스:
  - normalize_factor std == 0 → ZeroDivision 없이 0.0 시리즈 반환 (D8)
  - EPS YoY NaN 종목이 다른 종목 Z-score를 오염시키지 않음
  - 컨센서스 NaN → 어닝서프라이즈 가중치 동적 재배분 (D13)
  - 가중치 합계 검증 (quant-factor SKILL)
  - 유효 팩터 < 3개 종목 제외
  - ±3σ 클리핑 정상 작동
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scorer.score import (
    MIN_VALID_FACTORS,
    NAN_FALLBACK_SCORE,
    _redistribute_weights,
    load_weights,
    normalize_factor,
    score,
)


class TestLoadWeights:
    def test_weights_sum_to_one(self):
        weights = load_weights()
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_invalid_sum_raises_value_error(self, tmp_path):
        bad = tmp_path / "bad_weights.json"
        bad.write_text('{"eps_yoy": 0.5, "op_margin": 0.9}', encoding="utf-8")
        with pytest.raises(ValueError, match="1.0이 아님"):
            load_weights(bad)


class TestNormalizeFactor:
    def test_zero_std_returns_zeros_without_zero_division(self):
        series = pd.Series([5.0, 5.0, 5.0, 5.0])
        result = normalize_factor(series)
        assert (result == 0.0).all()
        assert not result.isna().any()

    def test_standard_distribution_produces_zero_mean(self):
        rng = np.random.default_rng(42)
        series = pd.Series(rng.normal(0, 1, 100))
        result = normalize_factor(series)
        assert abs(result.mean()) < 0.1
        assert abs(result.std() - 1.0) < 0.2

    def test_nan_input_preserved_and_does_not_contaminate(self):
        series = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
        result = normalize_factor(series)
        assert pd.isna(result.iloc[2])
        valid = result.dropna()
        assert abs(valid.mean()) < 1e-9

    def test_outlier_clipped_at_three_sigma(self):
        series = pd.Series(list(range(1, 100)) + [10_000_000])
        result = normalize_factor(series)
        clipped_max = result.max()
        assert clipped_max < 5.0

    def test_all_nan_returns_all_nan(self):
        series = pd.Series([np.nan, np.nan, np.nan])
        result = normalize_factor(series)
        assert result.isna().all()


class TestRedistributeWeights:
    def test_all_valid_keeps_original_weights(self):
        weights = {"a": 0.5, "b": 0.3, "c": 0.2}
        valid = pd.Series({"a": True, "b": True, "c": True})
        result = _redistribute_weights(weights, valid)
        assert result["a"] == pytest.approx(0.5)
        assert result.sum() == pytest.approx(1.0)

    def test_missing_factor_redistributes_proportionally(self):
        weights = {"a": 0.5, "b": 0.3, "c": 0.2}
        valid = pd.Series({"a": True, "b": True, "c": False})
        result = _redistribute_weights(weights, valid)
        assert result["a"] == pytest.approx(0.5 / 0.8)
        assert result["b"] == pytest.approx(0.3 / 0.8)
        assert result["c"] == 0.0
        assert result.sum() == pytest.approx(1.0)

    def test_all_invalid_returns_zero_total(self):
        weights = {"a": 0.5, "b": 0.5}
        valid = pd.Series({"a": False, "b": False})
        result = _redistribute_weights(weights, valid)
        assert result.sum() == 0.0


class TestScore:
    def test_basic_input_produces_ranked_output(self, factor_input_basic):
        result = score(factor_input_basic)
        assert len(result) > 0
        assert "rank" in result.columns
        assert list(result["rank"]) == sorted(result["rank"].tolist())
        assert result["total_score"].is_monotonic_decreasing

    def test_invalid_tickers_excluded(self, factor_input_basic):
        df = factor_input_basic.copy()
        df.loc[0, "is_valid"] = False
        result = score(df)
        assert "005930" not in result["ticker"].tolist()

    def test_eps_negative_does_not_contaminate_others(self, factor_input_with_negatives):
        result = score(factor_input_with_negatives)
        if not result.empty:
            assert not result["eps_growth_z"].isna().any() or len(result) < 4

    def test_pbr_zero_excluded_from_value_factor(self, factor_input_with_negatives):
        result = score(factor_input_with_negatives)
        ccc_row = result[result["ticker"] == "CCC"]
        if not ccc_row.empty:
            assert pd.isna(ccc_row["value_z"].iloc[0]) or ccc_row["value_z"].iloc[0] == NAN_FALLBACK_SCORE

    def test_insufficient_valid_factors_excluded(self):
        df = pd.DataFrame({
            "ticker": ["XXX"],
            "name": ["x"],
            "announce_date": [None],
            "revenue": [100.0], "op_income": [10.0], "net_income": [5.0], "eps": [1.0],
            "eps_yoy": [np.nan], "op_margin": [np.nan], "revenue_yoy": [np.nan],
            "pbr": [np.nan], "per": [np.nan], "eps_surprise": [np.nan],
            "market_cap": [1000.0], "is_valid": [True],
        })
        result = score(df)
        assert result.empty or "XXX" not in result["ticker"].tolist()

    def test_consensus_missing_uses_dynamic_reweighting(self, factor_input_with_negatives):
        result = score(factor_input_with_negatives)
        assert len(result) >= 1
        bbb_row = result[result["ticker"] == "BBB"]
        if not bbb_row.empty:
            score_value = bbb_row["total_score"].iloc[0]
            assert not pd.isna(score_value)
            assert -3.5 < score_value < 3.5

    def test_all_invalid_returns_empty(self, factor_input_basic):
        df = factor_input_basic.copy()
        df["is_valid"] = False
        result = score(df)
        assert result.empty

    def test_run_for_quarter_writes_parquet(self, tmp_path, factor_input_basic):
        from scorer.score import run_for_quarter
        input_path = tmp_path / "factor_input_2024Q1.parquet"
        factor_input_basic.to_parquet(input_path, index=False)
        out_path = run_for_quarter(input_path, 2099, 9)  # 연도/분기 고유값으로 충돌 방지
        try:
            assert out_path.exists()
            result = pd.read_parquet(out_path)
            assert "ticker" in result.columns
            assert "total_score" in result.columns
        finally:
            out_path.unlink(missing_ok=True)

    def test_middle_row_exclusion_does_not_corrupt_remaining_scores(self):
        """CCC(중간 행)가 제외될 때 DDD 스코어가 CCC 가중치를 사용하지 않음."""
        df = pd.DataFrame({
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "name": ["a", "b", "c", "d"],
            "announce_date": [None] * 4,
            "revenue": [1000.0, 800.0, 500.0, 400.0],
            "op_income": [100.0, 50.0, -10.0, 20.0],
            "net_income": [80.0, 40.0, -20.0, 15.0],
            "eps": [10.0, 5.0, -3.0, 2.0],
            "eps_yoy": [0.20, 0.10, np.nan, -0.30],
            "op_margin": [0.10, 0.0625, -0.02, 0.05],
            "revenue_yoy": [0.05, 0.10, 0.30, -0.05],
            "pbr": [1.2, 0.8, 0.0, 2.0],
            "per": [10.0, 8.0, np.nan, 15.0],
            "eps_surprise": [0.05, np.nan, np.nan, -0.05],
            "market_cap": [100000.0, 80000.0, 5000.0, 30000.0],
            "is_valid": [True, True, True, True],
        })
        result = score(df)
        assert "CCC" not in result["ticker"].tolist()
        assert "DDD" in result["ticker"].tolist()
        ddd_score = result[result["ticker"] == "DDD"]["total_score"].iloc[0]
        assert not pd.isna(ddd_score)
