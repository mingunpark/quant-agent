# TODOS

항목들은 우선순위 순. Approach B 백테스트 통과 후 진행 권장.

---

## TODO-01: Walk-forward / Out-of-sample 검증

**What**: 백테스트 기간을 train/test로 분리하여 과적합 방지 검증.

**Why**: 현재 계획은 2021~2024년 전체로 가중치를 설정하고 같은 기간을 백테스트함. In-sample 결과만으로는 전략의 실전 내구성을 확인할 수 없음.

**Pros**: 과적합 위험 정량화. 실전 투자 신뢰도 향상.

**Cons**: 샘플 기간이 짧아질수록 통계적 유의성 약화. 구현 공수 추가 ~1일.

**Context**: Approach B (알파 1차 확인) 통과 후 Approach A 전환 시점에 수행. 분할 예시 — train: 2021~2023, test: 2024.

**Depends on**: Approach B 백테스트 통과

---

## TODO-02: DART API / pykrx 실패 시 에러핸들링

**What**: 데이터 수집 중 API 실패 (rate limit, 차단, 키 만료) 시 try/except + 로깅 + 티커별 실패 목록 출력.

**Why**: 현재 실패 시 조용히 끊기거나 빈 DataFrame이 다음 레이어로 전달됨. 백테스트 결과가 실제로는 일부 종목 누락 상태일 수 있음.

**Pros**: 수집 신뢰성 확보. 재실행 시 실패 티커만 선택적으로 재수집 가능.

**Cons**: try/except 추가 시 코드 복잡도 소폭 증가.

**Context**: Approach B에서는 수동 실행 + 육안 확인으로 보완 가능. 자동화 파이프라인(Approach A) 전환 시 필수.

**Depends on**: data/collect.py 초기 구현 완료

---

## TODO-03: KOSDAQ150 지원

**What**: KOSPI200과 별도로 KOSDAQ150 유니버스 추가.

**Why**: CLAUDE.md 스펙에 "KOSPI200 또는 KOSDAQ150"이라고 명시되어 있으나 Approach B에서 제외.

**Pros**: 팩터 전략의 성장주(KOSDAQ) 적용 가능성 검증.

**Cons**: 데이터 수집/검증 공수 2배. KOSDAQ는 유동성 특성이 달라 별도 백테스트 필요.

**Depends on**: KOSPI200 백테스트 통과

---

## TODO-04: timing/ 레이어 구현 (기술적 타점)

**What**: scorer/ 상위 20종목에 RSI < 50, MA20 근접, 거래량 조건 타점 분석 추가.

**Why**: 팩터 스코어링만으로는 진입 타이밍이 나쁠 수 있음. 기술적 조건으로 추가 필터링 시 수익률 개선 가능성.

**Depends on**: Approach B 백테스트 통과 + Approach A 전환 결정

---

## TODO-05: `_load_latest_valuation()` 이중 parquet 로드 최적화

**What**: `data/process.py:build_factor_input()`에서 `_load_latest_valuation()`이 동일한 200개 price parquet을 분기당 2회 열고 있음 (현재 분기 + 전년도 동분기). 각 parquet을 1회 로드 후 두 스냅샷을 동시에 추출하도록 리팩터링.

**Why**: 12분기 처리 시 4,800회 파일 I/O → 2,400회로 절반 감소. 재실행 속도 개선.

**Pros**: processing 단계 I/O 50% 감소. 파일 로드 실패 시 오류 위치 명확화.

**Cons**: `_load_latest_valuation()` 내부 구조 변경 필요. 기존 테스트 영향 검토 필요.

**Depends on**: 백테스트 파이프라인 첫 실행 완료 후 성능 측정 선행

---

## TODO-06: 한국 공휴일 반영 (pandas-market-calendars)

**What**: `backtest/run.py:compute_signal_dates()`의 `announce_date + BDay(2)` 계산이 pandas BusinessDay 기준으로 한국 공휴일을 무시함. `pandas-market-calendars`의 XKRX 캘린더로 교체.

**Why**: 공휴일이 낀 경우 실제 진입일보다 최대 2일 이르게 계산될 수 있음 (설, 추석 연휴 등). 룩어헤드 바이어스 경계 위반 가능성.

**Pros**: 신호 날짜 정확도 향상. 실제 KRX 거래일 기준 T+2 보장.

**Cons**: `exchange-calendars` 패키지 추가 의존성. 과거 공휴일 데이터 범위 확인 필요.

**Context**: `backtest/run.py` L52 주석에 이미 언급됨. 백테스트 결과 유효성 확인 후 정밀도 개선 목적으로 적용.

**Depends on**: 백테스트 첫 실행 완료
