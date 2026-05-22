"""공용 픽스처: 외부 의존성 없이 scorer/validator 테스트 가능하게 함."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def factor_input_basic() -> pd.DataFrame:
    """5개 종목 정상 케이스. 모든 팩터 유효."""
    return pd.DataFrame({
        "ticker": ["005930", "000660", "035420", "035720", "051910"],
        "name": ["A", "B", "C", "D", "E"],
        "announce_date": [date(2024, 5, 14)] * 5,
        "revenue": [1000.0, 800.0, 500.0, 400.0, 600.0],
        "op_income": [100.0, 50.0, 80.0, 20.0, 60.0],
        "net_income": [80.0, 40.0, 60.0, 15.0, 50.0],
        "eps": [10.0, 5.0, 8.0, 2.0, 6.0],
        "eps_yoy": [0.20, 0.10, 0.50, -0.30, 0.05],
        "op_margin": [0.10, 0.0625, 0.16, 0.05, 0.10],
        "revenue_yoy": [0.05, 0.10, 0.30, -0.05, 0.08],
        "pbr": [1.2, 0.8, 5.0, 2.0, 1.5],
        "per": [10.0, 8.0, 25.0, 15.0, 12.0],
        "eps_surprise": [0.05, 0.02, 0.10, -0.05, 0.0],
        "market_cap": [100000.0, 80000.0, 50000.0, 30000.0, 40000.0],
        "is_valid": [True, True, True, True, True],
    })


@pytest.fixture
def factor_input_with_negatives() -> pd.DataFrame:
    """EPS 음수 + 컨센서스 NaN + PBR 0 종목 포함."""
    return pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC", "DDD"],
        "name": ["a", "b", "c", "d"],
        "announce_date": [date(2024, 5, 14)] * 4,
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
