"""KRX 지수 구성 종목 이력 파서 (KOSPI200 / KOSDAQ150).

KRX 홈페이지에서 분기별 구성 종목 CSV를 수동 다운로드한 후
data/raw/{index_name}_history/ 디렉토리에 배치하면 통합 parquet을 생성한다.

수동 다운로드 출처:
  https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd
    -> 통계 -> 기본 통계 -> 지수 -> 주가지수 -> 지수구성종목
    -> 지수명: KOSPI200 또는 KOSDAQ150, 조회일자: 각 분기 마지막 영업일
    -> CSV 다운로드 -> data/raw/{kospi200|kosdaq150}_history/{YYYYMMDD}.csv

서바이버십 바이어스 방지가 핵심. 자동화 안 됨 (Architecture D4 결정).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


SUPPORTED_INDICES = ("kospi200", "kosdaq150")

# 하위 호환용 기본값
RAW_DIR = Path(__file__).resolve().parent / "raw" / "kospi200_history"
OUT_PATH = Path(__file__).resolve().parent / "raw" / "kospi200_history.parquet"


@dataclass(frozen=True)
class UniverseSnapshot:
    snapshot_date: date
    tickers: tuple[str, ...]


def _index_paths(index_name: str) -> tuple[Path, Path]:
    """지수명 → (raw_dir, out_path) 반환."""
    name = index_name.lower()
    if name not in SUPPORTED_INDICES:
        raise ValueError(f"지원하지 않는 지수: {index_name}. 지원 목록: {SUPPORTED_INDICES}")
    base = Path(__file__).resolve().parent / "raw"
    return base / f"{name}_history", base / f"{name}_history.parquet"


def _extract_date_from_stem(stem: str) -> date:
    """파일명 stem에서 YYYYMMDD 패턴을 찾아 date 반환.

    KRX는 'YYYYMMDD.csv' 또는 'data_0923_YYYYMMDD.csv' 등 다양한 형식으로 다운로드됨.
    파일명 어디서든 8자리 숫자(유효한 날짜)를 찾아 사용.
    """
    # 파일명이 정확히 YYYYMMDD인 경우 (유효성 검증 포함)
    if len(stem) == 8 and stem.isdigit():
        try:
            return date.fromisoformat(f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}")
        except ValueError:
            pass  # 잘못된 날짜면 아래 regex로 fallthrough
    # 파일명 내 YYYYMMDD 패턴 검색 (예: data_0923_20260519)
    match = re.search(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", stem)
    if match:
        return date.fromisoformat(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    raise ValueError(
        f"파일명에서 날짜(YYYYMMDD)를 찾을 수 없음: '{stem}'\n"
        f"파일명에 날짜가 포함되어야 합니다. 예: '20260519.csv' 또는 'data_20260519.csv'"
    )


def _parse_krx_csv(csv_path: Path) -> UniverseSnapshot:
    """KRX CSV는 EUC-KR/CP949 인코딩, 첫 줄이 헤더, '단축코드' 컬럼에 6자리 종목코드.

    파일명에서 YYYYMMDD 패턴을 자동 탐색하므로 KRX의 다양한 파일명 형식을 지원.
    """
    snapshot_date = _extract_date_from_stem(csv_path.stem)
    try:
        df = pd.read_csv(csv_path, encoding="cp949", dtype=str)
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding="utf-8", dtype=str)

    ticker_col = next(
        (c for c in df.columns if c.strip() in ("단축코드", "종목코드", "Code")),
        None,
    )
    if ticker_col is None:
        raise ValueError(
            f"{csv_path.name}: '단축코드' 컬럼을 찾을 수 없음. KRX 양식 확인 필요"
        )
    tickers = tuple(t.zfill(6) for t in df[ticker_col].dropna().str.strip().tolist())
    if not tickers:
        raise ValueError(f"{csv_path.name}: 종목 0개. 파일 손상 가능")
    return UniverseSnapshot(snapshot_date=snapshot_date, tickers=tickers)


def build_universe_history(
    index_name: str,
    raw_dir: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    """raw_dir의 모든 KRX CSV를 통합하여 long-format parquet 생성.

    스키마: snapshot_date | ticker
    index_name: "kospi200" 또는 "kosdaq150"
    """
    default_raw, default_out = _index_paths(index_name)
    raw_dir = raw_dir or default_raw
    out_path = out_path or default_out

    if not raw_dir.exists():
        raise FileNotFoundError(
            f"{raw_dir}가 없음. KRX 홈페이지에서 {index_name.upper()} 구성 CSV를 "
            f"다운로드한 후 YYYYMMDD.csv 이름으로 배치하세요."
        )
    csv_files = sorted(raw_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"{raw_dir}에 CSV 파일이 없음")

    rows: list[dict] = []
    for csv_path in csv_files:
        snapshot = _parse_krx_csv(csv_path)
        for ticker in snapshot.tickers:
            rows.append({"snapshot_date": snapshot.snapshot_date, "ticker": ticker})

    df = pd.DataFrame(rows)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


def get_universe_for_quarter(
    snapshot_date: date,
    index_name: str = "kospi200",
    out_path: Path | None = None,
) -> list[str]:
    """주어진 분기 기준일의 구성 종목 반환 (PIT 룰).

    index_name: "kospi200" 또는 "kosdaq150"
    """
    _, default_out = _index_paths(index_name)
    out_path = out_path or default_out

    if not out_path.exists():
        raise FileNotFoundError(
            f"{out_path}가 없음. 먼저 build_universe_history('{index_name}')를 실행하세요."
        )
    df = pd.read_parquet(out_path)
    target = pd.Timestamp(snapshot_date)
    eligible_dates = df[df["snapshot_date"] <= target]["snapshot_date"].unique()
    if len(eligible_dates) == 0:
        raise ValueError(f"{snapshot_date} 이전 스냅샷 없음. 데이터 범위 확인")
    latest = max(eligible_dates)
    return df[df["snapshot_date"] == latest]["ticker"].tolist()


# 하위 호환 alias
def build_kospi200_history(raw_dir: Path = RAW_DIR, out_path: Path = OUT_PATH) -> Path:
    return build_universe_history("kospi200", raw_dir, out_path)


if __name__ == "__main__":
    import sys
    index = sys.argv[1] if len(sys.argv) > 1 else "kospi200"
    path = build_universe_history(index)
    print(f"{index.upper()} history written: {path}")
