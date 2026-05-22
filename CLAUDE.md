# 분기 실적 기반 종목 선별 에이전트

## 프로젝트 개요
분기 실적 데이터를 팩터로 스코어링하여 상위 20개 종목을 선별하고,
기술적 분석으로 매수 타점을 제시하는 에이전트 시스템.

---

## 디렉토리 구조
```
quant-agent/
├── CLAUDE.md                    ← 지금 이 파일 (공통 원칙)
├── data/
│   └── CLAUDE.md                ← Pipeline 1: 데이터 수집 레이어
├── scorer/
│   └── CLAUDE.md                ← Pipeline 2: 스코어링 엔진 레이어
├── timing/
│   └── CLAUDE.md                ← Pipeline 3: 매수 타점 레이어
├── backtest/
│   └── CLAUDE.md                ← 백테스트 검증 레이어
└── .claude/
    └── skills/
        ├── karpathy-guidelines/ ← 코딩 행동 교정 (전역)
        ├── quant-factor/        ← 팩터 계산 스킬
        ├── entry-timing/        ← 타점 분석 스킬
        └── backtest-validator/  ← 백테스트 검증 스킬
```

---

## [공통] 절대 제약 (전 레이어 적용)
- **룩어헤드 바이어스 절대 금지**: 실적 발표일 T+1 이후 데이터만 신호 생성에 사용
- **유니버스**: KOSPI200 또는 KOSDAQ150 내에서만 종목 선별
- **제외 필터**: 관리종목, 거래정지, 상장폐지 예고 종목 자동 제외
- **코드 언어**: Python 우선. 외부 라이브러리 추가 시 반드시 이유 명시

---

## [공통] 기술 스택
| 레이어 | 라이브러리 |
|--------|-----------|
| 데이터 수집 | OpenDartReader, pykrx, FinanceDataReader, dart-fss |
| 분석/스코어링 | pandas, numpy |
| 기술적 분석 | pandas-ta |
| 백테스트 | VectorBT, quantstats |
| 가격 예측 (선택) | Kronos-small (GPU 환경에서만) |

---

## [공통] Skills 참조
@.claude/skills/karpathy-guidelines/SKILL.md
@.claude/skills/quant-factor/SKILL.md
@.claude/skills/entry-timing/SKILL.md
@.claude/skills/backtest-validator/SKILL.md

---

## 레이어별 상세 → 해당 디렉토리의 CLAUDE.md 참조
- 데이터 수집: `@data/CLAUDE.md`
- 스코어링: `@scorer/CLAUDE.md`
- 타점 분석: `@timing/CLAUDE.md`
- 백테스트: `@backtest/CLAUDE.md`

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
