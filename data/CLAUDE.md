# Pipeline 1 — 데이터 수집 레이어

> 상위 공통 원칙: `@../CLAUDE.md`
> 관련 Skills: `@../.claude/skills/quant-factor/SKILL.md`

---

## 역할
분기 실적(DART), 주가, 밸류에이션 데이터를 수집하고
scorer/ 레이어가 바로 사용할 수 있는 형태로 정제하여 저장.

---

## 데이터 소스별 담당

### OpenDartReader — 분기 실적
```python
import OpenDartReader
dart = OpenDartReader(api_key)

# 분기 코드
# 11013: 1분기 / 11012: 반기 / 11014: 3분기 / 11011: 사업보고서(연간)
dart.finstate('005930, 000660', 2024, reprt_code='11013')
```
- 수집 항목: 매출액, 영업이익, 당기순이익, EPS
- 저장 형식: `data/raw/dart_{year}Q{quarter}.parquet`

### pykrx — 주가 + 밸류에이션
```python
from pykrx import stock
# PER, PBR, EPS, DIV, BPS 포함
stock.get_market_fundamental_by_date(start, end, ticker)
```
- 수집 항목: OHLCV, PER, PBR, EPS, 시가총액
- 저장 형식: `data/raw/price_{ticker}_{year}.parquet`

### FinanceDataReader — 어닝 서프라이즈 보조
- 컨센서스 대비 실제 EPS 차이 계산용
- 저장 형식: `data/raw/consensus_{year}Q{quarter}.parquet`

---

## 데이터 품질 규칙
1. **실적 발표일 수집 필수**: 종목별 실적 발표일을 반드시 함께 저장
   - 신호 생성은 발표일 T+1 이후에만 허용 (룩어헤드 바이어스 방지)
2. **결측치 처리**: 연결재무제표 없으면 dart-fss로 별도 파싱, 그래도 없으면 제외
3. **단위 통일**: 모든 금액은 백만원 단위로 정규화

---

## 출력 스키마
scorer/ 레이어에 전달하는 최종 DataFrame 형식:

```python
# data/processed/factor_input_{year}Q{quarter}.parquet
{
  'ticker':          str,   # 종목코드
  'name':            str,   # 종목명
  'announce_date':   date,  # 실적 발표일 (T+1 기준선)
  'revenue':         float, # 매출액 (백만원)
  'op_income':       float, # 영업이익 (백만원)
  'net_income':      float, # 당기순이익 (백만원)
  'eps':             float, # EPS
  'eps_yoy':         float, # EPS YoY 성장률
  'op_margin':       float, # 영업이익률
  'revenue_yoy':     float, # 매출 YoY 성장률
  'pbr':             float, # PBR
  'per':             float, # PER
  'eps_surprise':    float, # 어닝 서프라이즈 (%)
  'market_cap':      float, # 시가총액
  'is_valid':        bool,  # 관리종목/거래정지 제외 여부
}
```

---

## 구현 시 주의사항
- DART API 호출 제한: 분당 1000회 → 배치 요청 시 sleep(0.1) 적용
- pykrx는 스크래핑 방식이라 대량 수집 시 캐싱 필수
- API 키는 환경변수로 관리, 코드에 하드코딩 절대 금지
  ```
  DART_API_KEY=...  # .env 파일 또는 환경변수
  ```
