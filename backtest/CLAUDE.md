# 백테스트 검증 레이어

> 상위 공통 원칙: `@../CLAUDE.md`
> 관련 Skills: `@../.claude/skills/backtest-validator/SKILL.md`

---

## 역할
스코어링 전략과 타점 전략의 과거 유효성을 검증.
전략을 실전에 적용하기 전 반드시 이 레이어를 통과해야 함.

---

## 입력
```
scorer/output/top20_{year}Q{quarter}.parquet  (각 분기별)
data/raw/price_{ticker}_{year}.parquet
```

---

## 필수 검증 항목

### 1. 기본 요건
- **샘플 기간**: 최소 3년 (12분기) 이상
- **벤치마크**: KOSPI200 대비 비교 필수
- **거래비용**: 편도 0.015% + 슬리피지 0.1% 반드시 포함

### 2. 성과 지표 (quantstats 사용)
```python
import quantstats as qs

qs.reports.basic(
    portfolio_returns,
    benchmark=kospi200_returns,
    periods_per_year=4  # 분기 리밸런싱
)
```

| 지표 | 최소 기준 | 설명 |
|------|-----------|------|
| CAGR | > KOSPI200 + 3% | 벤치마크 초과 수익 |
| Sharpe Ratio | > 0.5 | 위험 대비 수익 |
| Max Drawdown | < -30% | 최대 낙폭 제한 |
| Win Rate | > 50% | 분기별 승률 |

### 3. 룩어헤드 바이어스 검증 (필수)
```python
def verify_no_lookahead(signals_df: pd.DataFrame) -> bool:
    """
    신호 생성일이 실적 발표일 T+1 이후인지 검증
    단 한 건이라도 위반 시 False 반환
    """
    violations = signals_df[
        signals_df['signal_date'] <= signals_df['announce_date']
    ]
    if len(violations) > 0:
        raise ValueError(f"룩어헤드 바이어스 위반: {len(violations)}건")
    return True
```

### 4. 연도별 성과 분해
특정 연도(예: 2020 코로나, 2022 금리인상)에만 성과가 집중되는지 확인.
```python
# 연도별 수익률 분해
annual_returns = portfolio_returns.resample('Y').apply(
    lambda x: (1 + x).prod() - 1
)
```

---

## VectorBT 백테스트 기본 구조
```python
import vectorbt as vbt

# 분기 리밸런싱 포트폴리오
pf = vbt.Portfolio.from_signals(
    close=prices,
    entries=entry_signals,
    exits=exit_signals,
    fees=0.00015,      # 편도 0.015%
    slippage=0.001,    # 슬리피지 0.1%
    freq='Q'
)
print(pf.stats())
```

---

## 출력
```
backtest/results/
├── summary_{strategy}_{period}.html     # quantstats 전체 리포트
├── annual_breakdown_{strategy}.csv      # 연도별 성과 분해
└── lookahead_check_{strategy}.log       # 룩어헤드 바이어스 검증 로그
```

---

## 구현 시 주의사항
- 백테스트 결과가 기준 미달이면 scorer/ 팩터 가중치 조정 후 재검증
- 과최적화(overfitting) 방지: 파라미터 조정은 최대 3회까지만
- 백테스트 통과 후에도 실전에서는 소액으로 검증 기간 운용 권장
- 결과 해석 시 한계 명시 필수:
  "과거 성과가 미래를 보장하지 않음. 특히 2020년 이후 유동성 장세 영향 고려 필요"
