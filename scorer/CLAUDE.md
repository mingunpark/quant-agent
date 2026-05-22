# Pipeline 2 — 스코어링 엔진 레이어

> 상위 공통 원칙: `@../CLAUDE.md`
> 관련 Skills: `@../.claude/skills/quant-factor/SKILL.md`

---

## 역할
data/ 레이어에서 받은 팩터 데이터를 스코어링하여
상위 20개 종목 리스트를 생성하고 timing/ 레이어로 전달.

---

## 입력
```
data/processed/factor_input_{year}Q{quarter}.parquet
```

---

## 팩터 구성 및 가중치

| 팩터 | 계산식 | 가중치 | 방향 |
|------|--------|--------|------|
| EPS 성장률 YoY | `(eps_t - eps_t-4) / abs(eps_t-4)` | 30% | 높을수록 좋음 |
| 영업이익률 | `op_income / revenue` | 25% | 높을수록 좋음 |
| 매출 성장률 YoY | `(revenue_t - revenue_t-4) / revenue_t-4` | 20% | 높을수록 좋음 |
| 저PBR (밸류) | `1 / pbr` | 15% | 높을수록 좋음 |
| 어닝 서프라이즈 | `(eps_actual - eps_consensus) / abs(eps_consensus)` | 10% | 높을수록 좋음 |

### 정규화 방법
- 각 팩터별 **Z-score 정규화** 후 가중합
- 이상치 처리: ±3σ 초과값은 ±3σ로 클리핑
- EPS가 음수인 종목의 EPS 성장률: 제외 처리 (NaN → 최하위 스코어)

```python
def normalize_factor(series: pd.Series) -> pd.Series:
    mean, std = series.mean(), series.std()
    if std == 0:
        return pd.Series(0.0, index=series.index)
    clipped = series.clip(lower=mean - 3 * std, upper=mean + 3 * std)
    return (clipped - mean) / std
```

---

## 종목 선별 로직

```python
# 1. 유효 종목 필터
df = df[df['is_valid'] == True]

# 2. 팩터별 Z-score 계산
for factor, weight in FACTOR_WEIGHTS.items():
    df[f'{factor}_z'] = normalize_factor(df[factor])

# 3. 종합 스코어 계산
df['total_score'] = sum(
    df[f'{factor}_z'] * weight
    for factor, weight in FACTOR_WEIGHTS.items()
)

# 4. 상위 20 선별
top20 = df.nlargest(20, 'total_score')[['ticker', 'name', 'total_score', ...]]
```

---

## 출력 스키마
timing/ 레이어에 전달하는 최종 DataFrame:

```python
# scorer/output/top20_{year}Q{quarter}.parquet
{
  'rank':           int,    # 순위 (1~20)
  'ticker':         str,    # 종목코드
  'name':           str,    # 종목명
  'total_score':    float,  # 종합 스코어
  'eps_growth_z':   float,  # EPS 성장률 Z-score
  'op_margin_z':    float,  # 영업이익률 Z-score
  'rev_growth_z':   float,  # 매출 성장률 Z-score
  'value_z':        float,  # 밸류 Z-score
  'surprise_z':     float,  # 어닝서프라이즈 Z-score
  'announce_date':  date,   # 실적 발표일 (타점 분석 기준선)
  'pbr':            float,  # 원본 PBR
  'per':            float,  # 원본 PER
}
```

---

## 구현 시 주의사항
- 팩터 가중치는 `config/factor_weights.json`으로 분리 관리 (하드코딩 금지)
- 리밸런싱 기준일: 어닝시즌 종료 후 고정 날짜 사용
  - 1분기 실적 → 5월 중순 / 2분기 → 8월 중순 / 3분기 → 11월 중순 / 연간 → 2월 중순
- 스코어 계산 시 사용된 팩터 개수가 3개 미만인 종목은 제외
