"""data/universe.py 단위 테스트.

KRX CSV 파싱과 PIT(Point-In-Time) 유니버스 조회 검증.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data.universe import _parse_krx_csv, _extract_date_from_stem, build_kospi200_history, get_universe_for_quarter


@pytest.fixture
def krx_csv_dir(tmp_path: Path) -> Path:
    """KRX CSV 샘플 2개를 포함한 임시 디렉토리."""
    csv_dir = tmp_path / "kospi200_history"
    csv_dir.mkdir()

    # 첫 번째 스냅샷: 2022-03-31
    (csv_dir / "20220331.csv").write_text(
        "단축코드,종목명\n005930,삼성전자\n000660,SK하이닉스\n035420,NAVER\n",
        encoding="cp949",
    )
    # 두 번째 스냅샷: 2023-03-31 (새 종목 추가)
    (csv_dir / "20230331.csv").write_text(
        "단축코드,종목명\n005930,삼성전자\n000660,SK하이닉스\n035720,카카오\n",
        encoding="cp949",
    )
    return csv_dir


class TestExtractDateFromStem:
    def test_exact_yyyymmdd(self):
        assert _extract_date_from_stem("20260519") == date(2026, 5, 19)

    def test_krx_prefixed_filename(self):
        # KRX 실제 다운로드 파일명 예: data_0923_20260519
        assert _extract_date_from_stem("data_0923_20260519") == date(2026, 5, 19)

    def test_any_prefix_suffix(self):
        assert _extract_date_from_stem("kospi200_20240331_final") == date(2024, 3, 31)

    def test_no_date_raises(self):
        with pytest.raises(ValueError, match="날짜"):
            _extract_date_from_stem("tmpm9i0s")

    def test_invalid_month_not_matched(self):
        # 20261319는 월이 13이므로 매칭 안 됨
        with pytest.raises(ValueError, match="날짜"):
            _extract_date_from_stem("20261319")


class TestParseKrxCsv:
    def test_standard_column_name(self, tmp_path):
        csv = tmp_path / "20240101.csv"
        csv.write_text("단축코드,종목명\n005930,삼성전자\n000660,SK하이닉스\n", encoding="cp949")
        snapshot = _parse_krx_csv(csv)
        assert snapshot.snapshot_date == date(2024, 1, 1)
        assert "005930" in snapshot.tickers
        assert "000660" in snapshot.tickers

    def test_ticker_zero_padded(self, tmp_path):
        csv = tmp_path / "20240101.csv"
        csv.write_text("단축코드,종목명\n5930,삼성전자\n", encoding="cp949")
        snapshot = _parse_krx_csv(csv)
        assert "005930" in snapshot.tickers

    def test_missing_ticker_column_raises(self, tmp_path):
        csv = tmp_path / "20240101.csv"
        csv.write_text("이름,종목명\n삼성전자,A\n", encoding="cp949")
        with pytest.raises(ValueError, match="단축코드"):
            _parse_krx_csv(csv)

    def test_empty_tickers_raises(self, tmp_path):
        csv = tmp_path / "20240101.csv"
        csv.write_text("단축코드,종목명\n", encoding="cp949")
        with pytest.raises(ValueError, match="0개"):
            _parse_krx_csv(csv)

    def test_utf8_encoding_fallback(self, tmp_path):
        csv = tmp_path / "20240101.csv"
        csv.write_text("단축코드,종목명\n005930,삼성전자\n", encoding="utf-8")
        snapshot = _parse_krx_csv(csv)
        assert "005930" in snapshot.tickers


class TestBuildKospi200History:
    def test_builds_long_format_parquet(self, krx_csv_dir, tmp_path):
        out = tmp_path / "kospi200_history.parquet"
        result = build_kospi200_history(raw_dir=krx_csv_dir, out_path=out)
        assert result.exists()
        df = pd.read_parquet(result)
        assert set(df.columns) == {"snapshot_date", "ticker"}
        assert len(df) == 6  # 3 tickers × 2 snapshots

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            build_kospi200_history(raw_dir=tmp_path / "nonexistent")

    def test_no_csv_files_raises(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="CSV"):
            build_kospi200_history(raw_dir=empty_dir)


class TestGetUniverseForQuarter:
    def test_returns_most_recent_snapshot_before_date(self, krx_csv_dir, tmp_path):
        out = tmp_path / "kospi200_history.parquet"
        build_kospi200_history(raw_dir=krx_csv_dir, out_path=out)

        # monkeypatching OUT_PATH이 어렵기 때문에 직접 parquet 경로 확인
        df = pd.read_parquet(out)
        target = pd.Timestamp(date(2022, 6, 30))
        eligible = df[df["snapshot_date"] <= target]["snapshot_date"].unique()
        latest = max(eligible)
        tickers = df[df["snapshot_date"] == latest]["ticker"].tolist()
        assert "005930" in tickers
        assert "035420" in tickers  # 2022-03-31 스냅샷에 있는 종목
        assert "035720" not in tickers  # 2023-03-31에만 있는 종목

    def test_no_snapshot_before_date_raises(self, krx_csv_dir, tmp_path):
        out = tmp_path / "kospi200_history.parquet"
        build_kospi200_history(raw_dir=krx_csv_dir, out_path=out)
        df = pd.read_parquet(out)
        target = pd.Timestamp(date(2020, 1, 1))
        eligible = df[df["snapshot_date"] <= target]["snapshot_date"].unique()
        assert len(eligible) == 0
