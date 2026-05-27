"""분기 실적 기반 종목 선별 에이전트 — Streamlit 웹 앱.

실행: streamlit run app.py
"""

from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ─── 페이지 설정 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="퀀트 종목 선별 에이전트",
    page_icon="📈",
    layout="wide",
)

FACTOR_INPUT_COLUMNS = [
    "ticker", "name", "announce_date",
    "revenue", "op_income", "net_income", "eps",
    "eps_yoy", "op_margin", "revenue_yoy",
    "pbr", "per", "eps_surprise", "market_cap", "is_valid",
]

RESULTS_DIR = Path("backtest/results")

INDEX_LABELS = {
    "kospi200": "KOSPI200",
    "kosdaq150": "KOSDAQ150",
}


# ─── 공용 함수 ──────────────────────────────────────────────────────────────────
@st.cache_data
def make_excel_template(market_label: str) -> bytes:
    """팩터 입력 Excel 템플릿 생성."""
    if market_label == "KOSPI200":
        sample_rows = [
            {"ticker": "005930", "name": "삼성전자", "announce_date": date(2024, 5, 14),
             "revenue": 71914000.0, "op_income": 6609000.0, "net_income": 5174000.0,
             "eps": 772.0, "eps_yoy": 0.12, "op_margin": 0.092, "revenue_yoy": 0.05,
             "pbr": 1.3, "per": 18.5, "eps_surprise": 0.03,
             "market_cap": 440000000.0, "is_valid": True},
            {"ticker": "000660", "name": "SK하이닉스", "announce_date": date(2024, 5, 14),
             "revenue": 12431000.0, "op_income": 2890000.0, "net_income": 2100000.0,
             "eps": 2880.0, "eps_yoy": 0.35, "op_margin": 0.233, "revenue_yoy": 0.18,
             "pbr": 1.8, "per": 22.0, "eps_surprise": 0.07,
             "market_cap": 210000000.0, "is_valid": True},
        ]
    else:
        sample_rows = [
            {"ticker": "247540", "name": "에코프로비엠", "announce_date": date(2024, 5, 14),
             "revenue": 1200000.0, "op_income": 80000.0, "net_income": 60000.0,
             "eps": 3200.0, "eps_yoy": 0.25, "op_margin": 0.067, "revenue_yoy": 0.20,
             "pbr": 4.2, "per": 35.0, "eps_surprise": 0.05,
             "market_cap": 12000000.0, "is_valid": True},
            {"ticker": "086520", "name": "에코프로", "announce_date": date(2024, 5, 14),
             "revenue": 900000.0, "op_income": 55000.0, "net_income": 40000.0,
             "eps": 2100.0, "eps_yoy": 0.18, "op_margin": 0.061, "revenue_yoy": 0.15,
             "pbr": 5.1, "per": 42.0, "eps_surprise": np.nan,
             "market_cap": 8500000.0, "is_valid": True},
        ]

    sample = pd.DataFrame(sample_rows)
    guide = pd.DataFrame({
        "컬럼": FACTOR_INPUT_COLUMNS,
        "설명": [
            "종목코드 (6자리)", "종목명", "실적 발표일 (YYYY-MM-DD)",
            "매출액 (백만원)", "영업이익 (백만원)", "당기순이익 (백만원)",
            "EPS (원)", "EPS YoY 성장률 (소수점, 예: 0.12 = +12%)",
            "영업이익률 (소수점)", "매출 YoY 성장률 (소수점)",
            "PBR", "PER", "어닝 서프라이즈 (없으면 빈 칸)",
            "시가총액 (백만원)", "유효 종목 여부 (TRUE/FALSE)",
        ],
    })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sample.to_excel(writer, index=False, sheet_name="factor_input")
        guide.to_excel(writer, index=False, sheet_name="컬럼 설명")
    return buf.getvalue()


def load_factor_input(uploaded_file) -> pd.DataFrame | None:
    try:
        df = pd.read_excel(uploaded_file, sheet_name="factor_input", dtype={"ticker": str})
        missing = [c for c in FACTOR_INPUT_COLUMNS if c not in df.columns]
        if missing:
            st.error(f"필수 컬럼 누락: {missing}")
            return None
        df["ticker"] = df["ticker"].str.zfill(6)
        df["name"] = df["name"].fillna("").astype(str)
        df["is_valid"] = df["is_valid"].astype(bool)
        df["announce_date"] = pd.to_datetime(df["announce_date"]).dt.date
        return df
    except Exception as e:
        st.error(f"파일 읽기 실패: {e}")
        return None


def run_scoring(factor_df: pd.DataFrame, weights: dict, market_label: str) -> pd.DataFrame | None:
    from scorer.score import score
    try:
        result = score(factor_df, weights, top_n=10)
        if result.empty:
            st.warning(f"{market_label}: 스코어링 결과가 없습니다. 유효 종목을 확인하세요.")
            return None
        result.insert(0, "시장", market_label)
        return result
    except Exception as e:
        st.error(f"{market_label} 스코어링 실패: {e}")
        return None


def _infer_quarter_from_df(df: pd.DataFrame, filename: str = "") -> tuple[int, int] | None:
    """announce_date 컬럼에서 연도/분기 자동 추정. 실패 시 파일명에서 추출."""
    import re
    if "announce_date" in df.columns:
        dates = pd.to_datetime(df["announce_date"], errors="coerce").dropna()
        if not dates.empty:
            period = dates.dt.to_period("Q").mode()
            if not period.empty:
                p = period.iloc[0]
                return p.year, p.quarter
    # 파일명 폴백: "2024Q1", "2024Q3" 패턴
    m = re.search(r"(\d{4})Q([1-4])", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _load_wide_prices(tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
    """캐시된 가격 parquet → wide DataFrame (index=date, columns=ticker)."""
    raw_price_dir = Path("data/raw/price")
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    frames: dict[str, pd.Series] = {}
    for ticker in tickers:
        candidates = list(raw_price_dir.glob(f"price_{ticker}_*.parquet"))
        if not candidates:
            continue
        df = pd.read_parquet(candidates[0])
        df.index = pd.to_datetime(df.index)
        mask = (df.index >= start_ts) & (df.index <= end_ts)
        if "close" not in df.columns:
            continue
        s = df[mask]["close"]
        if len(s) > 0:
            frames[ticker] = s
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames)


def _download_benchmark_returns(start_date: date, end_date: date) -> pd.Series:
    """KOSPI200 지수 일별 수익률 다운로드 (pykrx 티커 1028)."""
    try:
        from pykrx import stock
        idx = stock.get_index_ohlcv_by_date(
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
            "1028",
        )
        if idx.empty:
            return pd.Series(dtype=float)
        return idx["종가"].pct_change().dropna()
    except Exception:
        return pd.Series(dtype=float)


def render_score_table(result: pd.DataFrame, market_label: str):
    import plotly.express as px

    rename = {
        "rank": "순위", "ticker": "종목코드", "name": "종목명",
        "total_score": "종합점수", "eps_growth_z": "EPS성장",
        "op_margin_z": "영업이익률", "rev_growth_z": "매출성장",
        "value_z": "밸류", "surprise_z": "어닝서프라이즈",
        "announce_date": "발표일",
    }
    display_cols = [c for c in rename if c in result.columns]
    display = result[display_cols].rename(columns=rename).copy()
    display["종합점수"] = display["종합점수"].round(3)

    st.dataframe(
        display.style.background_gradient(subset=["종합점수"], cmap="RdYlGn"),
        use_container_width=True,
        hide_index=True,
    )

    chart_df = result.copy()
    chart_df["ticker"] = chart_df["ticker"].astype(str)
    chart_df["label"] = chart_df["ticker"] + "\n" + chart_df["name"].fillna("")
    fig = px.bar(
        chart_df,
        x="label",
        y="total_score",
        color="total_score",
        color_continuous_scale="RdYlGn",
        text=chart_df["total_score"].round(2),
        labels={"label": "종목", "total_score": "종합점수"},
        title=f"{market_label} 상위 10 종합점수",
        category_orders={"label": chart_df["label"].tolist()},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(showlegend=False, height=320, margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)


# ─── 파이프라인 상태 초기화 ────────────────────────────────────────────────────
if "saved_universes" not in st.session_state:
    st.session_state["saved_universes"] = set()  # {"kospi200", "kosdaq150"}

def _universe_ready(index_name: str) -> bool:
    """저장된 parquet 파일이 실제로 존재하는지 확인."""
    from data.universe import _index_paths
    _, out_path = _index_paths(index_name)
    return out_path.exists()


# ─── 헤더 + 파이프라인 진행 표시 ────────────────────────────────────────────────
st.title("퀀트 종목 선별 에이전트")
st.caption("KOSPI200 TOP 10 + KOSDAQ150 TOP 10 — 분기 실적 기반 팩터 스코어링")

step0_done = "collect_factor_path" in st.session_state
step1_done = _universe_ready("kospi200") or _universe_ready("kosdaq150")
step2_done = "score_results" in st.session_state
step3_done = "bt_run_result" in st.session_state

c0, c1, c2, c3 = st.columns(4)
with c0:
    if step0_done:
        st.success("0단계 데이터 수집 완료")
    else:
        st.info("0단계 데이터 수집 (선택)")
with c1:
    if step1_done:
        st.success("1단계 유니버스 설정 완료")
    else:
        st.info("1단계 유니버스 설정")
with c2:
    if step2_done:
        st.success("2단계 팩터 스코어링 완료")
    elif step1_done:
        st.warning("2단계 팩터 스코어링 — 진행 필요")
    else:
        st.info("2단계 팩터 스코어링")
with c3:
    if step3_done:
        st.success("3단계 백테스트 완료")
    elif step2_done:
        st.warning("3단계 백테스트 — 진행 가능")
    else:
        st.info("3단계 백테스트 (선택)")

st.divider()

tab_collect, tab_universe, tab_scoring, tab_backtest = st.tabs(
    ["0단계 — 데이터 수집", "1단계 — 유니버스 설정", "2단계 — 팩터 스코어링", "3단계 — 백테스트 결과"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# 탭 0: 데이터 수집
# ═══════════════════════════════════════════════════════════════════════════════
with tab_collect:
    st.header("DART 재무 데이터 수집")
    st.info(
        "DART Open API로 분기 재무 데이터를 수집하고 팩터 입력 Excel을 자동 생성합니다.\n\n"
        "**참고**: 유니버스(1단계)가 먼저 설정되어 있어야 시장별 종목 목록을 가져올 수 있습니다.\n"
        "수집이 완료된 분기는 재실행 시 건너뜁니다."
    )

    # DART API 키
    dart_key_input = st.text_input(
        "DART API 키",
        type="password",
        value=os.getenv("DART_API_KEY", ""),
        placeholder="https://opendart.fss.or.kr 에서 발급",
        help="입력한 키는 수집 실행 시에만 사용되며 저장되지 않습니다.",
    )

    # 수집 설정
    col_yr, col_qt = st.columns(2)
    with col_yr:
        collect_year = st.selectbox("연도", list(range(2026, 2019, -1)), key="collect_year_sel")
    with col_qt:
        collect_quarter = st.selectbox("분기", [1, 2, 3, 4], key="collect_quarter_sel")

    # 유니버스 확인
    avail_markets = [m for m in ("kospi200", "kosdaq150") if _universe_ready(m)]
    if not avail_markets:
        st.warning(
            "1단계에서 KOSPI200 또는 KOSDAQ150 유니버스를 먼저 설정하세요. "
            "유니버스가 없으면 수집할 종목 목록을 알 수 없습니다."
        )
    else:
        selected_markets = st.multiselect(
            "수집할 시장",
            options=[INDEX_LABELS[m] for m in avail_markets],
            default=[INDEX_LABELS[m] for m in avail_markets],
            key="collect_markets_sel",
        )

        if not dart_key_input:
            st.warning("DART API 키를 입력하세요.")
        elif not selected_markets:
            st.info("수집할 시장을 선택하세요.")
        else:
            if st.button("DART 수집 시작", type="primary", key="btn_collect"):
                from data.universe import _index_paths
                from data.collect import (
                    collect_dart_quarter as _collect_dart,
                    collect_consensus as _collect_consensus,
                    collect_announce_dates as _collect_announce,
                )
                from data.process import build_factor_input

                label_to_index = {v: k for k, v in INDEX_LABELS.items()}

                # 종목 목록 취합 (시장별 분리 보관 → 다운로드 시 활용)
                all_tickers: list[str] = []
                market_tickers_map: dict[str, list[str]] = {}
                for market_label in selected_markets:
                    idx_name = label_to_index[market_label]
                    _, uni_path = _index_paths(idx_name)
                    uni_df = pd.read_parquet(uni_path)
                    latest_date = uni_df["snapshot_date"].max()
                    tickers = uni_df[uni_df["snapshot_date"] == latest_date]["ticker"].tolist()
                    market_tickers_map[market_label] = tickers
                    all_tickers.extend(tickers)
                all_tickers = list(dict.fromkeys(all_tickers))  # 중복 제거 (순서 유지)

                total_tickers = len(all_tickers)
                progress_bar = st.progress(0)
                status_text = st.empty()

                # API 키를 현재 프로세스 환경변수에 임시 주입 (완료 후 복원)
                _prev_dart_key = os.environ.get("DART_API_KEY")
                os.environ["DART_API_KEY"] = dart_key_input

                try:
                    def _make_cb(offset: int, label: str):
                        def cb(i: int, total: int, ticker: str):
                            global_i = offset + i
                            global_total = max(total_tickers * 2, 1)
                            progress_bar.progress(min(global_i / global_total, 1.0))
                            status_text.text(f"{label} 수집 중: {ticker} ({min(i + 1, total)}/{total})")
                        return cb

                    # 현재 분기
                    status_text.text(f"{collect_year}Q{collect_quarter} DART 수집 중...")
                    _collect_dart(
                        collect_year, collect_quarter, all_tickers,
                        _make_cb(0, f"{collect_year}Q{collect_quarter}"),
                    )

                    # 전년 동기 (YoY 기준)
                    status_text.text(f"{collect_year - 1}Q{collect_quarter} DART 수집 중 (YoY 기준)...")
                    _collect_dart(
                        collect_year - 1, collect_quarter, all_tickers,
                        _make_cb(total_tickers, f"{collect_year - 1}Q{collect_quarter}"),
                    )

                    # 접수일(announce_date) 일괄 조회 — /list.json 2~3회 호출로 전체 수집
                    progress_bar.progress(0.65)
                    status_text.text("실적 발표일(announce_date) 수집 중...")
                    try:
                        _collect_announce(collect_year, collect_quarter)
                        _collect_announce(collect_year - 1, collect_quarter)
                    except Exception as _ann_e:
                        st.warning(f"발표일 수집 실패 (announce_date 비어있을 수 있음): {_ann_e}")

                    # 컨센서스 수집
                    progress_bar.progress(0.75)
                    status_text.text("컨센서스 데이터 수집 중...")
                    _collect_consensus(collect_year, collect_quarter, all_tickers)

                    # 가격/밸류에이션 수집 (PBR, PER, market_cap, EPS 확보)
                    from data.collect import collect_price as _collect_price
                    from datetime import date as _date
                    _price_start = _date(collect_year - 1, 1, 1)
                    _price_end = _date(collect_year, 12, 31)
                    _price_fails: list[str] = []
                    for _pi, _pt in enumerate(all_tickers):
                        prog = 0.75 + 0.20 * (_pi + 1) / max(total_tickers, 1)
                        progress_bar.progress(min(prog, 0.95))
                        if _pi % 10 == 0:
                            status_text.text(
                                f"가격/밸류에이션 수집 중... ({_pi + 1}/{total_tickers}) "
                                "— PBR·PER·시가총액 확보"
                            )
                        try:
                            _collect_price(_pt, _price_start, _price_end)
                        except Exception as _pe:
                            _price_fails.append(f"{_pt}({_pe})")
                    if _price_fails:
                        st.warning(
                            f"가격 수집 실패 {len(_price_fails)}종목 "
                            f"(PBR/PER/시가총액 비어있을 수 있음): "
                            f"{', '.join(_price_fails[:5])}{'...' if len(_price_fails) > 5 else ''}"
                        )

                    # 팩터 입력 생성
                    progress_bar.progress(0.97)
                    status_text.text("팩터 입력 생성 중 (종목명 조회 포함)...")
                    factor_path = build_factor_input(collect_year, collect_quarter, all_tickers)

                    progress_bar.progress(1.0)
                    status_text.text("완료!")
                    st.session_state["collect_factor_path"] = str(factor_path)
                    st.session_state["collect_year_val"] = collect_year
                    st.session_state["collect_quarter_val"] = collect_quarter
                    st.session_state["collect_market_tickers"] = market_tickers_map
                    st.success(
                        f"수집 완료: {total_tickers}개 종목 처리, "
                        f"{collect_year}Q{collect_quarter} + {collect_year - 1}Q{collect_quarter}"
                    )

                except Exception as _e:
                    progress_bar.empty()
                    status_text.empty()
                    st.error(f"수집 실패: {_e}")
                finally:
                    if _prev_dart_key is None:
                        os.environ.pop("DART_API_KEY", None)
                    else:
                        os.environ["DART_API_KEY"] = _prev_dart_key

        # 수집 완료 후 결과 표시
        if "collect_factor_path" in st.session_state:
            _factor_path = Path(st.session_state["collect_factor_path"])
            _yr = st.session_state.get("collect_year_val", "?")
            _qt = st.session_state.get("collect_quarter_val", "?")

            if _factor_path.exists():
                _factor_df = pd.read_parquet(_factor_path)
                valid_count = int(_factor_df["is_valid"].sum()) if "is_valid" in _factor_df.columns else 0

                st.divider()
                st.subheader(f"수집 결과 — {_yr}Q{_qt}")
                col_m1, col_m2 = st.columns(2)
                col_m1.metric("전체 종목", len(_factor_df))
                col_m2.metric("유효 종목", valid_count)

                # 팩터 Excel 다운로드 — 시장별 분리
                _market_tickers = st.session_state.get("collect_market_tickers", {})
                if _market_tickers:
                    _dl_cols = st.columns(len(_market_tickers))
                    for _dc, (_ml, _mt) in zip(_dl_cols, _market_tickers.items()):
                        _mdf = _factor_df[_factor_df["ticker"].isin(_mt)].copy()
                        _mdf["is_valid"] = True
                        _mbuf = io.BytesIO()
                        with pd.ExcelWriter(_mbuf, engine="openpyxl") as _mw:
                            _mdf.to_excel(_mw, index=False, sheet_name="factor_input")
                        _dc.download_button(
                            f"{_ml} 팩터 Excel",
                            data=_mbuf.getvalue(),
                            file_name=f"factor_input_{_ml.lower()}_{_yr}Q{_qt}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_{_ml.lower()}_excel",
                        )
                else:
                    _buf = io.BytesIO()
                    with pd.ExcelWriter(_buf, engine="openpyxl") as _writer:
                        _factor_df.to_excel(_writer, index=False, sheet_name="factor_input")
                    st.download_button(
                        "팩터 입력 Excel 다운로드",
                        data=_buf.getvalue(),
                        file_name=f"factor_input_{_yr}Q{_qt}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_factor_excel",
                    )

                # 네이버 금융 교차 검증
                st.divider()
                st.subheader("네이버 금융 교차 검증 (5개 샘플)")
                st.caption(
                    "DART 수집 데이터의 PER/PBR을 네이버 금융 실시간 값과 비교합니다. "
                    "15% 이상 차이 시 주의 표시 — 단, 주가 변동으로 인한 차이는 정상입니다."
                )

                if st.button("교차 검증 실행", key="btn_naver_validate"):
                    from data.naver_validate import validate_against_naver

                    _nv_progress = st.progress(0)
                    _nv_status = st.empty()

                    def _nv_cb(i: int, total: int, ticker: str):
                        _nv_progress.progress(min((i + 1) / max(total, 1), 1.0))
                        _nv_status.text(f"네이버 조회 중: {ticker} ({i + 1}/{total})")

                    with st.spinner("네이버 금융에서 데이터 조회 중..."):
                        try:
                            _nv_result = validate_against_naver(
                                _factor_df, sample_n=5, progress_callback=_nv_cb
                            )
                            st.session_state["naver_validation"] = _nv_result
                            _nv_progress.empty()
                            _nv_status.empty()
                        except Exception as _nv_e:
                            st.error(f"교차 검증 실패: {_nv_e}")

                if "naver_validation" in st.session_state:
                    _nv_df: pd.DataFrame = st.session_state["naver_validation"]
                    has_price_missing = (_nv_df["경고"] == "가격 미수집").any()
                    has_warning = (_nv_df["경고"] == "주의").any()
                    if has_price_missing:
                        st.info(
                            "가격 데이터(PER/PBR)가 아직 수집되지 않아 Naver 현재값을 참조합니다. "
                            "0단계에서 가격 수집을 완료하면 정확한 교차 검증이 가능합니다."
                        )
                    if has_warning:
                        st.warning("15% 이상 차이가 발생한 종목이 있습니다. 데이터를 직접 확인하세요.")
                    elif not has_price_missing:
                        st.success("모든 샘플 종목이 정상 범위 내에 있습니다.")

                    def _style_warning(row):
                        if row["경고"] == "주의":
                            return ["background-color: #fff3cd"] * len(row)
                        if row["경고"] == "가격 미수집":
                            return ["background-color: #f0f0f0"] * len(row)
                        return [""] * len(row)

                    st.dataframe(
                        _nv_df.style.apply(_style_warning, axis=1),
                        use_container_width=True,
                        hide_index=True,
                    )

    # 0단계 하단 안내
    st.divider()
    if "collect_factor_path" in st.session_state:
        st.info(
            "**팩터 Excel 생성 완료.**\n\n"
            "**다음 단계**: 위의 **'팩터 입력 Excel 다운로드'** 버튼으로 파일을 받은 뒤 "
            "**'2단계 — 팩터 스코어링'** 탭에 업로드하세요.\n\n"
            "KRX 유니버스가 아직 설정되지 않았다면 **'1단계 — 유니버스 설정'** 탭을 먼저 완료하세요."
        )
    else:
        st.info(
            "이 단계는 선택 사항입니다. 직접 팩터 Excel을 준비했다면 **'2단계 — 팩터 스코어링'**으로 바로 이동할 수 있습니다."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 탭 1: 유니버스 설정
# ═══════════════════════════════════════════════════════════════════════════════
with tab_universe:
    st.header("유니버스 설정")
    st.info(
        "KRX 홈페이지에서 각 지수의 구성 종목 CSV를 다운로드하여 업로드하세요.\n\n"
        "**경로**: https://data.krx.co.kr → 통계 → 기본통계 → 지수 → 주가지수 → 지수구성종목\n"
        "→ 조회일자: 각 분기 마지막 영업일 → CSV 다운로드"
    )

    col_kp, col_kq = st.columns(2)

    for col, index_name, label in [
        (col_kp, "kospi200", "KOSPI200"),
        (col_kq, "kosdaq150", "KOSDAQ150"),
    ]:
        with col:
            st.subheader(label)
            uploaded = st.file_uploader(
                f"{label} 구성 종목 CSV (복수 업로드 가능)",
                type=["csv"],
                accept_multiple_files=True,
                key=f"krx_{index_name}",
            )

            if uploaded:
                from data.universe import _parse_krx_csv
                import tempfile

                snapshots = []
                errors = []
                raw_bytes = {}
                with tempfile.TemporaryDirectory() as _tmp:
                    tmp_dir = Path(_tmp)
                    for f in uploaded:
                        content = f.read()
                        raw_bytes[f.name] = content
                        # 원본 파일명 보존 → _parse_krx_csv가 파일명에서 날짜 추출 가능
                        tmp_path = tmp_dir / f.name
                        tmp_path.write_bytes(content)
                        try:
                            snap = _parse_krx_csv(tmp_path)
                            snapshots.append({
                                "스냅샷 날짜": snap.snapshot_date,
                                "종목 수": len(snap.tickers),
                            })
                        except Exception as e:
                            errors.append(f"{f.name}: {e}")

                if errors:
                    st.error("\n".join(errors))
                if snapshots:
                    st.success(f"{len(snapshots)}개 스냅샷 파싱 완료")
                    st.dataframe(pd.DataFrame(snapshots), hide_index=True, use_container_width=True)

                    if st.button(f"{label} 저장", key=f"save_{index_name}"):
                        from data.universe import build_universe_history, _index_paths
                        dest, _ = _index_paths(index_name)
                        dest.mkdir(parents=True, exist_ok=True)
                        for fname, content in raw_bytes.items():
                            (dest / fname).write_bytes(content)
                        try:
                            build_universe_history(index_name)
                            st.session_state["saved_universes"].add(index_name)
                            st.success(f"{label} 유니버스 저장 완료 ({len(snapshots)}개 스냅샷)")
                            st.rerun()
                        except Exception as e:
                            st.error(f"저장 실패: {e}")

                # 저장 완료된 지수는 완료 배지 표시
                if _universe_ready(index_name):
                    st.success(f"{label} 유니버스 준비됨")


    # 탭 1 하단: 다음 단계 안내
    st.divider()
    if _universe_ready("kospi200") or _universe_ready("kosdaq150"):
        ready_labels = []
        if _universe_ready("kospi200"):
            ready_labels.append("KOSPI200")
        if _universe_ready("kosdaq150"):
            ready_labels.append("KOSDAQ150")
        st.info(
            f"**{' / '.join(ready_labels)} 유니버스 준비 완료.**\n\n"
            "**다음 단계**: 상단의 **'2단계 — 팩터 스코어링'** 탭으로 이동하여\n"
            "각 시장의 팩터 입력 Excel 파일을 업로드하세요.\n\n"
            "팩터 입력 Excel이 없다면 탭에서 **템플릿을 다운로드**하여 작성할 수 있습니다."
        )
    else:
        st.info("KOSPI200 또는 KOSDAQ150 CSV를 업로드하고 저장하면 다음 단계로 진행할 수 있습니다.")


# ═══════════════════════════════════════════════════════════════════════════════
# 탭 2: 팩터 스코어링
# ═══════════════════════════════════════════════════════════════════════════════
with tab_scoring:
    st.header("팩터 스코어링")

    _FACTOR_KO = {
        "eps_yoy": "EPS YoY 성장률",
        "op_margin": "영업이익률",
        "revenue_yoy": "매출 YoY 성장률",
        "value": "저PBR (밸류)",
        "eps_surprise": "어닝 서프라이즈",
    }

    # 가중치 표시
    with st.expander("현재 팩터 가중치", expanded=False):
        from scorer.score import load_weights
        try:
            weights = load_weights()
            wdf = pd.DataFrame({
                "팩터": [_FACTOR_KO.get(k, k) for k in weights.keys()],
                "가중치": [f"{v:.0%}" for v in weights.values()],
            })
            st.dataframe(wdf, hide_index=True, use_container_width=True)
        except Exception as e:
            st.error(f"가중치 로드 실패: {e}")
            weights = None

    st.divider()

    # 두 시장 나란히 업로드
    col_kp, col_kq = st.columns(2)
    uploaded = {}

    for col, index_name, label in [
        (col_kp, "kospi200", "KOSPI200"),
        (col_kq, "kosdaq150", "KOSDAQ150"),
    ]:
        with col:
            st.subheader(label)
            st.download_button(
                f"{label} 템플릿 다운로드",
                data=make_excel_template(label),
                file_name=f"factor_input_{index_name}_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"tmpl_{index_name}",
            )
            f = st.file_uploader(
                f"{label} 팩터 입력 Excel (.xlsx)",
                type=["xlsx"],
                key=f"excel_{index_name}",
            )
            uploaded[index_name] = f

            if f:
                df = load_factor_input(f)
                if df is not None:
                    st.caption(f"종목 수: {len(df)} / 유효: {df['is_valid'].sum()}")
                    with st.expander("미리보기", expanded=False):
                        st.dataframe(df, use_container_width=True)
                    uploaded[index_name] = df
                else:
                    uploaded[index_name] = None

    st.divider()

    has_any = any(isinstance(v, pd.DataFrame) for v in uploaded.values())
    if has_any and weights:
        if st.button("스코어링 실행", type="primary", key="run_scoring"):
            results = {}
            with st.spinner("스코어링 중..."):
                for index_name, label in [("kospi200", "KOSPI200"), ("kosdaq150", "KOSDAQ150")]:
                    df = uploaded.get(index_name)
                    if isinstance(df, pd.DataFrame):
                        r = run_scoring(df, weights, label)
                        if r is not None:
                            results[index_name] = r
            if results:
                st.session_state["score_results"] = results
                st.success("스코어링 완료 — 아래에서 결과를 확인하세요.")
                st.info(
                    "결과를 확인한 후 **'3단계 — 백테스트 결과'** 탭에서 "
                    "전략의 과거 성과를 검증할 수 있습니다."
                )
    else:
        st.info(
            "최소 하나의 시장 Excel을 업로드하고 스코어링을 실행하세요.\n\n"
            "팩터 입력 Excel이 없다면 **템플릿 다운로드** 버튼으로 양식을 받아 작성하세요."
        )

    # 결과 표시
    if "score_results" in st.session_state:
        results: dict = st.session_state["score_results"]
        st.divider()

        tab_kp, tab_kq, tab_combined = st.tabs(["KOSPI200 TOP 10", "KOSDAQ150 TOP 10", "통합 20선"])

        with tab_kp:
            if "kospi200" in results:
                render_score_table(results["kospi200"], "KOSPI200")
            else:
                st.info("KOSPI200 데이터가 없습니다.")

        with tab_kq:
            if "kosdaq150" in results:
                render_score_table(results["kosdaq150"], "KOSDAQ150")
            else:
                st.info("KOSDAQ150 데이터가 없습니다.")

        with tab_combined:
            import plotly.express as px

            frames = list(results.values())
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined = combined.sort_values("total_score", ascending=False).reset_index(drop=True)
                combined.insert(0, "순위", combined.index + 1)

                rename = {
                    "시장": "시장", "ticker": "종목코드", "name": "종목명",
                    "total_score": "종합점수", "announce_date": "발표일",
                }
                display = combined[[c for c in rename if c in combined.columns]].rename(columns=rename)
                display["종합점수"] = display["종합점수"].round(3)

                st.dataframe(
                    display.style.background_gradient(subset=["종합점수"], cmap="RdYlGn"),
                    use_container_width=True,
                    hide_index=True,
                )

                fig = px.bar(
                    combined,
                    x="ticker",
                    y="total_score",
                    color="시장",
                    color_discrete_map={"KOSPI200": "#1f77b4", "KOSDAQ150": "#ff7f0e"},
                    text=combined["total_score"].round(2),
                    labels={"ticker": "종목코드", "total_score": "종합점수"},
                    title="KOSPI200 + KOSDAQ150 통합 20선",
                )
                fig.update_traces(textposition="outside")
                fig.update_layout(height=380, margin=dict(t=40, b=10))
                st.plotly_chart(fig, use_container_width=True)

                # 다운로드
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    if "kospi200" in results:
                        results["kospi200"].to_excel(writer, index=False, sheet_name="KOSPI200_TOP10")
                    if "kosdaq150" in results:
                        results["kosdaq150"].to_excel(writer, index=False, sheet_name="KOSDAQ150_TOP10")
                    combined.to_excel(writer, index=False, sheet_name="통합20선")
                st.download_button(
                    "전체 결과 Excel 다운로드",
                    data=buf.getvalue(),
                    file_name="scoring_result.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )


# ═══════════════════════════════════════════════════════════════════════════════
# 탭 3: 백테스트
# ═══════════════════════════════════════════════════════════════════════════════
with tab_backtest:
    st.header("백테스트")

    # VectorBT 설치 여부 사전 확인
    try:
        import vectorbt as _vbt  # noqa: F401
        _vbt_ok = True
    except ImportError:
        _vbt_ok = False
        st.error(
            "vectorbt가 설치되지 않았습니다. 백테스트를 실행하려면:\n"
            "```\npip install vectorbt\n```"
        )

    # ── 1. 분기별 팩터 데이터 ───────────────────────────────────────────
    st.subheader("1. 분기별 팩터 데이터")
    st.caption(
        "2단계와 동일한 형식의 팩터 입력 Excel을 분기별로 업로드하세요. "
        "신뢰할 수 있는 백테스트를 위해 12분기(3년) 이상 권장합니다."
    )

    _bt_uploads = st.file_uploader(
        "분기별 팩터 Excel (복수 업로드 가능)",
        type=["xlsx"],
        accept_multiple_files=True,
        key="bt_excel_multi",
    )

    _quarterly_tops: dict[tuple[int, int], pd.DataFrame] = {}

    if _bt_uploads:
        from scorer.score import load_weights as _bt_load_weights, score as _bt_score
        try:
            _bt_weights = _bt_load_weights()
        except Exception as _bwe:
            st.error(f"가중치 로드 실패: {_bwe}")
            _bt_weights = None

        if _bt_weights:
            _parse_errors: list[str] = []
            for _bfu in _bt_uploads:
                _bfdf = load_factor_input(_bfu)
                if _bfdf is None:
                    _parse_errors.append(f"{_bfu.name}: 파싱 실패")
                    continue
                _yq = _infer_quarter_from_df(_bfdf, _bfu.name)
                if _yq is None:
                    _parse_errors.append(f"{_bfu.name}: 분기 추정 불가 (파일명에 연도Q분기 형식 포함 또는 announce_date 확인)")
                    continue
                try:
                    _top = _bt_score(_bfdf, _bt_weights, top_n=10)
                    if not _top.empty:
                        _quarterly_tops[_yq] = _top
                    else:
                        _parse_errors.append(f"{_bfu.name}: 스코어링 결과 없음")
                except Exception as _bse:
                    _parse_errors.append(f"{_bfu.name}: 스코어링 실패 ({_bse})")

            for _pe in _parse_errors:
                st.warning(_pe)

            if _quarterly_tops:
                _qsummary = pd.DataFrame([
                    {
                        "연도": y, "분기": q,
                        "상위 종목 수": len(v),
                        "1위 종목": f"{v.iloc[0]['ticker']} {v.iloc[0]['name']}" if not v.empty else "-",
                    }
                    for (y, q), v in sorted(_quarterly_tops.items())
                ])
                st.success(f"{len(_quarterly_tops)}개 분기 로드 완료")
                if len(_quarterly_tops) < 4:
                    st.warning("12분기(3년) 미만 데이터입니다. 결과 신뢰도가 낮을 수 있습니다.")
                st.dataframe(_qsummary, hide_index=True, use_container_width=True)

    # ── 2. 백테스트 기간 ───────────────────────────────────────────────
    st.subheader("2. 백테스트 기간")
    _btc1, _btc2 = st.columns(2)
    _bt_start = _btc1.date_input("시작일", value=date(2022, 1, 1), key="bt_start_date")
    _bt_end = _btc2.date_input("종료일", value=date(2024, 12, 31), key="bt_end_date")

    if _quarterly_tops:
        # ── 3. 가격 데이터 ─────────────────────────────────────────────
        st.subheader("3. 가격 데이터")

        _all_bt_tickers: list[str] = list(dict.fromkeys(
            t for _top_df in _quarterly_tops.values() for t in _top_df["ticker"].tolist()
        ))

        _raw_price_dir = Path("data/raw/price")
        _cached = [t for t in _all_bt_tickers if list(_raw_price_dir.glob(f"price_{t}_*.parquet"))]
        _missing = [t for t in _all_bt_tickers if t not in _cached]

        _pc1, _pc2, _pc3 = st.columns(3)
        _pc1.metric("전체 종목", len(_all_bt_tickers))
        _pc2.metric("캐시됨", len(_cached))
        _pc3.metric("다운로드 필요", len(_missing))

        if _missing:
            _miss_preview = ", ".join(_missing[:8]) + ("..." if len(_missing) > 8 else "")
            st.warning(f"가격 데이터 없음: {_miss_preview}")
            if st.button("pykrx에서 가격 다운로드", key="btn_bt_price_dl"):
                from data.collect import collect_price as _bt_collect_price
                _pr_prog = st.progress(0)
                _pr_status = st.empty()
                _pr_fails: list[str] = []
                for _pi, _pt in enumerate(_missing):
                    _pr_status.text(f"다운로드 중: {_pt} ({_pi + 1}/{len(_missing)})")
                    _pr_prog.progress((_pi + 1) / len(_missing))
                    try:
                        _bt_collect_price(_pt, _bt_start, _bt_end)
                    except Exception:
                        _pr_fails.append(_pt)
                _pr_prog.empty()
                _pr_status.empty()
                if _pr_fails:
                    st.warning(f"다운로드 실패 ({len(_pr_fails)}개): {', '.join(_pr_fails[:5])}")
                else:
                    st.success("가격 데이터 다운로드 완료")
                st.rerun()
        else:
            st.success("모든 종목 가격 데이터 준비됨")

        # ── 4. 백테스트 실행 ───────────────────────────────────────────
        st.subheader("4. 백테스트 실행")
        _bt_strategy = st.text_input(
            "전략명", value="factor_top10", key="bt_strategy_name",
            help="결과 파일명에 사용됩니다."
        )

        if st.button("백테스트 실행", type="primary", key="btn_run_bt", disabled=not _vbt_ok):
            with st.spinner("신호 생성 중..."):
                from backtest.run import compute_signal_dates as _compute_sigs
                _signals_df = _compute_sigs(_quarterly_tops)

            if _signals_df.empty:
                st.error("신호 생성 실패: announce_date가 없는 종목이 많습니다.")
            else:
                st.caption(f"생성된 신호: {len(_signals_df)}건")
                with st.spinner("가격 데이터 로드 중..."):
                    _wide_prices = _load_wide_prices(_all_bt_tickers, _bt_start, _bt_end)

                if _wide_prices.empty:
                    st.error("가격 데이터 없음. 먼저 가격 데이터를 다운로드하세요.")
                else:
                    with st.spinner("벤치마크(KOSPI200) 다운로드 중..."):
                        _bm_returns = _download_benchmark_returns(_bt_start, _bt_end)

                    with st.spinner("VectorBT 백테스트 실행 중..."):
                        try:
                            from backtest.run import run_backtest as _run_bt
                            _bt_result = _run_bt(
                                _signals_df, _wide_prices, _bm_returns,
                                strategy_name=_bt_strategy,
                            )
                            st.session_state["bt_run_result"] = _bt_result
                            st.rerun()
                        except Exception as _be:
                            st.error(f"백테스트 실행 실패: {_be}")

    # ── 실행 결과 표시 ─────────────────────────────────────────────────
    if "bt_run_result" in st.session_state:
        import plotly.express as px

        _r = st.session_state["bt_run_result"]
        _cagr = _r.get("cagr", 0.0)
        _bm_cagr = _r.get("benchmark_cagr", 0.0)
        _sharpe = _r.get("sharpe", 0.0)
        _mdd = _r.get("mdd", 0.0)
        _perf = _r.get("performance_pass", {})

        st.divider()
        st.subheader("백테스트 결과")

        _rm1, _rm2, _rm3, _rm4 = st.columns(4)
        _rm1.metric("CAGR", f"{_cagr:.2%}", f"{_cagr - _bm_cagr:+.2%} vs 벤치마크")
        _rm2.metric("벤치마크 CAGR", f"{_bm_cagr:.2%}")
        _rm3.metric("Sharpe Ratio", f"{_sharpe:.3f}")
        _rm4.metric("Max Drawdown", f"{_mdd:.2%}")

        _rf1, _rf2, _rf3 = st.columns(3)
        (_rf1.success if _perf.get("cagr_pass") else _rf1.error)("CAGR > 벤치마크+3%")
        (_rf2.success if _perf.get("sharpe_pass") else _rf2.error)("Sharpe >= 0.5")
        (_rf3.success if _perf.get("mdd_pass") else _rf3.error)("MDD >= -30%")

        _bt_artifacts = _r.get("artifacts", {})
        _annual_csv = _bt_artifacts.get("annual")
        if _annual_csv and Path(_annual_csv).exists():
            _adf = pd.read_csv(_annual_csv, index_col=0)
            _adf.index = pd.to_datetime(_adf.index).year
            _adf.columns = ["수익률"]
            _afig = px.bar(
                _adf.reset_index(), x="index", y="수익률",
                color="수익률", color_continuous_scale="RdYlGn",
                labels={"index": "연도"},
                title="연도별 수익률",
            )
            _afig.update_layout(height=320, margin=dict(t=40, b=10))
            st.plotly_chart(_afig, use_container_width=True)

        _summary_txt = _bt_artifacts.get("summary")
        if _summary_txt and Path(_summary_txt).exists():
            with st.expander("전체 결과 텍스트"):
                st.text(Path(_summary_txt).read_text(encoding="utf-8"))

        if st.button("결과 초기화", key="btn_bt_reset"):
            del st.session_state["bt_run_result"]
            st.rerun()

        st.caption(
            "과거 성과는 미래를 보장하지 않으며, "
            "특히 2020년 이후 유동성 장세 영향을 고려해야 합니다."
        )

    # ── 저장된 결과 파일 (보조) ────────────────────────────────────────
    _summary_files = sorted(RESULTS_DIR.glob("summary_*.txt")) if RESULTS_DIR.exists() else []
    if _summary_files:
        st.divider()
        st.subheader("저장된 백테스트 결과")
        _sel_file = st.selectbox(
            "결과 파일 선택",
            options=[f.name for f in _summary_files],
            key="bt_select_saved",
        )
        if _sel_file:
            _sel_text = (RESULTS_DIR / _sel_file).read_text(encoding="utf-8")
            _sel_lines = {
                line.split(":")[0].strip(): line.split(":", 1)[1].strip()
                for line in _sel_text.splitlines()
                if ":" in line and not line.startswith("---") and not line.startswith("과거")
            }
            _sc1, _sc2, _sc3, _sc4 = st.columns(4)
            try:
                _scagr = float(_sel_lines.get("CAGR", "0%").replace("%", "")) / 100
                _sbm = float(_sel_lines.get("Benchmark CAGR", "0%").replace("%", "")) / 100
                _ssharpe = float(_sel_lines.get("Sharpe Ratio", "0"))
                _smdd = float(_sel_lines.get("Max Drawdown", "0%").replace("%", "")) / 100
                _sc1.metric("CAGR", f"{_scagr:.2%}", f"{_scagr - _sbm:+.2%} vs 벤치마크")
                _sc2.metric("벤치마크 CAGR", f"{_sbm:.2%}")
                _sc3.metric("Sharpe Ratio", f"{_ssharpe:.3f}")
                _sc4.metric("Max Drawdown", f"{_smdd:.2%}")
            except (ValueError, KeyError):
                pass

            _ann_name = _sel_file.replace("summary_", "annual_breakdown_").replace(".txt", ".csv")
            _ann_path = RESULTS_DIR / _ann_name
            if _ann_path.exists():
                import plotly.express as px
                _sadf = pd.read_csv(_ann_path, index_col=0)
                _sadf.index = pd.to_datetime(_sadf.index).year
                _sadf.columns = ["수익률"]
                _sfig = px.bar(
                    _sadf.reset_index(), x="index", y="수익률",
                    color="수익률", color_continuous_scale="RdYlGn",
                    labels={"index": "연도"},
                    title="연도별 수익률",
                )
                _sfig.update_layout(height=320, margin=dict(t=40, b=10))
                st.plotly_chart(_sfig, use_container_width=True)

            with st.expander("전체 결과 텍스트"):
                st.text(_sel_text)
