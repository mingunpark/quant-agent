"""팩터 스코어링 엔진 (T3 + T5).

정규화/스코어링 흐름:
  is_valid 필터 -> 팩터별 Z-score (±3σ 클리핑) -> 동적 가중치 -> 상위 20

핵심 결정사항:
  D5: eps_t-4 = 0 또는 consensus = 0 → 해당 팩터 NaN → 최하위 스코어 (-3.0)
  D8: std == 0 → Z-score 0.0 시리즈 반환
  D13: 종목별로 NaN 팩터를 제외하고 남은 팩터 가중치를 합 = 1로 재정규화
       (Outside Voice가 지적한 "Z=0 채우면 상수 보고" 문제 회피)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "factor_weights.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

MIN_VALID_FACTORS = 2
NAN_FALLBACK_SCORE = -3.0
TOP_N = 10

FACTOR_TO_COLUMN = {
    "eps_yoy": "eps_yoy",
    "op_margin": "op_margin",
    "revenue_yoy": "revenue_yoy",
    "value": "value",
    "eps_surprise": "eps_surprise",
}


def load_weights(path: Path = CONFIG_PATH) -> dict[str, float]:
    """factor_weights.json 로드 + 합계 1.0 검증 (quant-factor SKILL 체크리스트)."""
    with path.open("r", encoding="utf-8") as f:
        weights = json.load(f)
    total = sum(weights.values())
    if not np.isclose(total, 1.0, atol=1e-6):
        raise ValueError(f"factor_weights 합계가 1.0이 아님: {total}")
    return weights


def normalize_factor(series: pd.Series) -> pd.Series:
    """±3σ 클리핑 후 Z-score 정규화.

    std == 0 (모든 종목 동일값) → 0.0 시리즈 반환 (Code Quality D8).
    NaN 값은 보존되어 dynamic weighting 단계에서 처리됨.
    """
    valid = series.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index)
    mean, std = valid.mean(), valid.std()
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index).where(series.notna(), np.nan)
    clipped = valid.clip(lower=mean - 3 * std, upper=mean + 3 * std)
    z = (clipped - mean) / std
    return z.reindex(series.index)


def _value_factor(pbr: pd.Series) -> pd.Series:
    """저PBR 팩터: 1 / pbr. pbr <= 0이면 NaN (자본잠식 종목 제외)."""
    safe = pbr.where(pbr > 0, np.nan)
    return 1.0 / safe


def _redistribute_weights(weights: dict[str, float], valid_mask: pd.Series) -> pd.Series:
    """종목별로 NaN인 팩터의 가중치를 유효 팩터에 비례 재배분 (D13).

    valid_mask: index = factor 이름, 값 = True/False (해당 종목의 해당 팩터 유효 여부)
    반환: index = factor 이름, 값 = 재조정된 가중치 (합계 = 1.0 또는 0.0)
    """
    weight_series = pd.Series(weights)
    effective = weight_series.where(valid_mask, 0.0)
    total = effective.sum()
    if total == 0:
        return effective
    return effective / total


def score(
    factor_input: pd.DataFrame,
    weights: dict[str, float] | None = None,
    top_n: int = TOP_N,
) -> pd.DataFrame:
    """factor_input → 종목별 total_score 부여 + 상위 N 선별.

    출력 스키마 (scorer/CLAUDE.md):
      rank, ticker, name, total_score, eps_growth_z, op_margin_z, rev_growth_z,
      value_z, surprise_z, announce_date, pbr, per
    """
    if weights is None:
        weights = load_weights()

    df = factor_input.copy()
    df = df[df["is_valid"]].reset_index(drop=True)
    if df.empty:
        return _empty_result()

    df["value"] = _value_factor(df["pbr"])

    z_columns: dict[str, str] = {}
    for factor in weights.keys():
        column = FACTOR_TO_COLUMN[factor]
        z_col = f"{factor}_z"
        df[z_col] = normalize_factor(df[column])
        z_columns[factor] = z_col

    factor_validity = pd.DataFrame(
        {factor: df[z_columns[factor]].notna() for factor in weights.keys()}
    )
    valid_counts = factor_validity.sum(axis=1)
    mask = valid_counts >= MIN_VALID_FACTORS
    factor_validity = factor_validity[mask].reset_index(drop=True)
    df = df[mask].reset_index(drop=True)
    if df.empty:
        return _empty_result()

    scores: list[float] = []
    for i in df.index:
        per_factor_valid = factor_validity.loc[i]
        adjusted = _redistribute_weights(weights, per_factor_valid)
        contributions = []
        for factor, weight in adjusted.items():
            z = df.at[i, z_columns[factor]]
            if pd.isna(z):
                contributions.append(NAN_FALLBACK_SCORE * weight if weight > 0 else 0.0)
            else:
                contributions.append(z * weight)
        scores.append(float(np.sum(contributions)))
    df["total_score"] = scores

    df = df.sort_values("total_score", ascending=False).head(top_n).reset_index(drop=True)
    df["rank"] = df.index + 1

    rename_map = {
        "eps_yoy_z": "eps_growth_z",
        "op_margin_z": "op_margin_z",
        "revenue_yoy_z": "rev_growth_z",
        "value_z": "value_z",
        "eps_surprise_z": "surprise_z",
    }
    df = df.rename(columns=rename_map)

    output_cols = [
        "rank", "ticker", "name", "total_score",
        "eps_growth_z", "op_margin_z", "rev_growth_z", "value_z", "surprise_z",
        "announce_date", "pbr", "per",
    ]
    return df.reindex(columns=output_cols)


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "rank", "ticker", "name", "total_score",
        "eps_growth_z", "op_margin_z", "rev_growth_z", "value_z", "surprise_z",
        "announce_date", "pbr", "per",
    ])


def run_for_quarter(factor_input_path: Path, year: int, quarter: int) -> Path:
    """data/processed → scorer/output 파이프라인 진입점."""
    factor_input = pd.read_parquet(factor_input_path)
    weights = load_weights()
    result = score(factor_input, weights)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"top{TOP_N}_{year}Q{quarter}.parquet"
    result.to_parquet(out_path, index=False)
    return out_path
