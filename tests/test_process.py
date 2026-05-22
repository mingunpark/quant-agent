"""data/process.py 단위 테스트.

외부 API 없이 로컬 parquet/CSV 파일만으로 검증 가능한 함수들.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from data.process import _safe_yoy, _safe_div


class TestSafeYoy:
    def test_normal_positive_growth(self):
        assert _safe_yoy(120.0, 100.0) == pytest.approx(0.20)

    def test_normal_negative_growth(self):
        assert _safe_yoy(80.0, 100.0) == pytest.approx(-0.20)

    def test_previous_zero_returns_nan(self):
        assert math.isnan(_safe_yoy(100.0, 0.0))

    def test_previous_nan_returns_nan(self):
        assert math.isnan(_safe_yoy(100.0, float("nan")))

    def test_current_nan_returns_nan(self):
        assert math.isnan(_safe_yoy(float("nan"), 100.0))

    def test_none_previous_returns_nan(self):
        assert math.isnan(_safe_yoy(100.0, None))

    def test_none_current_returns_nan(self):
        assert math.isnan(_safe_yoy(None, 100.0))

    def test_negative_denominator_uses_abs(self):
        # (50 - (-100)) / abs(-100) = 150 / 100 = 1.5
        assert _safe_yoy(50.0, -100.0) == pytest.approx(1.5)

    def test_string_input_returns_nan(self):
        assert math.isnan(_safe_yoy("invalid", 100.0))


class TestSafeDiv:
    def test_normal_division(self):
        assert _safe_div(10.0, 100.0) == pytest.approx(0.10)

    def test_denominator_zero_returns_nan(self):
        assert math.isnan(_safe_div(10.0, 0.0))

    def test_denominator_nan_returns_nan(self):
        assert math.isnan(_safe_div(10.0, float("nan")))

    def test_numerator_nan_returns_nan(self):
        assert math.isnan(_safe_div(float("nan"), 100.0))

    def test_none_input_returns_nan(self):
        assert math.isnan(_safe_div(None, 100.0))
        assert math.isnan(_safe_div(10.0, None))

    def test_negative_result(self):
        assert _safe_div(-10.0, 100.0) == pytest.approx(-0.10)
