# Pipeline 3 — 매수 타점 레이어

> 상위 공통 원칙: `@../CLAUDE.md`
> 관련 Skills: `@../.claude/skills/entry-timing/SKILL.md`

---

## 역할
scorer/ 레이어에서 선별된 상위 20종목에 대해
기술적 분석으로 매수 타점을 제시.
(선택) Kronos로 단기 가격 방향성 보조.

---

## 입력
```
scorer/output/top20_{year}Q{quarter}.parquet
```

---

## 타점 분석 방법론

### 기본 타점 조건 (pandas-ta 사용)
아래 조건을 **모두 만족**할 때 매수 신호:

| 조건 | 기준 | 의미 |
|------|------|------|
| RSI | < 50 | 과매수 구간 아님 |
| 20일 이동평균 | 현재가 ≥ MA20 × 0.97 | MA20 근접 또는 위 |
| 거래량 | 5일 평균 ≥ 평상시 80% | 거래 위축 아님 |
| 실적발표 후 경과 | ≥ T+1 | 룩어헤드 바이어스 방지 |

```python
import pandas_ta as ta

def get_entry_signal(ohlcv: pd.DataFrame) -> dict:
    ohlcv['rsi'] = ta.rsi(ohlcv['close'], length=14)
    ohlcv['ma20'] = ta.sma(ohlcv['close'], length=20)
    ohlcv['vol_ma5'] = ohlcv['volume'].rolling(5).mean()

    latest = ohlcv.iloc[-1]
    rsi_ok  = latest['rsi'] < 50
    ma20_ok = latest['close'] >= latest['ma20'] * 0.97
    vol_ok  = latest['volume'] >= latest['vol_ma5'] * 0.8
    return {
        'rsi_ok':        rsi_ok,
        'ma20_ok':       ma20_ok,
        'vol_ok':        vol_ok,
        'entry_ok':      all([rsi_ok, ma20_ok, vol_ok]),
        'rsi':           latest['rsi'],
        'ma20':          latest['ma20'],
        'suggest_price': latest['ma20'],
    }
```

### 지지선 기반 목표가 / 손절선
- **목표가**: 52주 고점의 80% (보수적 기준)
- **손절선**: 진입가 - 7% (기계적 손절)

---

## Kronos 보조 분석 (선택, GPU 환경에서만)

GPU가 없으면 이 섹션 건너뜀. 기본 타점 조건만으로 충분.

```python
# GPU 확인 후 조건부 실행
import torch
USE_KRONOS = torch.cuda.is_available()

if USE_KRONOS:
    from model import Kronos, KronosTokenizer, KronosPredictor
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, device="cuda:0", max_context=512)
    # 향후 5일 예측 → 하락 예상 시 진입 지연 플래그 추가
```

Kronos 사용 시 추가 출력 필드:
- `kronos_5d_direction`: 'UP' / 'DOWN' / 'NEUTRAL'
- `entry_delay_flag`: True면 진입 1~3일 지연 권고

---

## 출력 스키마
최종 리포트 형식:

```python
# timing/output/entry_signals_{date}.json
[
  {
    "rank":            1,
    "ticker":          "005930",
    "name":            "삼성전자",
    "total_score":     2.34,
    "entry_ok":        True,
    "suggest_price":   75800,      # 타점 제안가 (MA20 기준)
    "current_price":   76200,
    "rsi":             44.2,
    "ma20":            75800,
    "target_price":    89000,      # 목표가
    "stop_loss":       70800,      # 손절가 (-7%)
    "entry_delay_flag": False,     # Kronos 없으면 항상 False
    "kronos_5d":       null,       # GPU 없으면 null
    "announce_date":   "2025-05-14"
  },
  ...
]
```

---

## 구현 시 주의사항
- 기술적 지표 계산에 필요한 최소 데이터: 60거래일 이상
- 데이터 부족 종목(최근 상장 등)은 타점 분석 제외, 리포트에 명시
- Kronos 없어도 에이전트가 정상 동작해야 함 (graceful degradation)
- 매수 타점 미충족 종목도 리포트에 포함 (entry_ok=False로 표시)
