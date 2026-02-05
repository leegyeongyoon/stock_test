# Upbit 자동매매 시스템

Upbit KRW 현물 자동매매 시스템 + FastAPI + Next.js 대시보드

## 기술 스택

- **Trading Engine**: Python 3.11+ (asyncio)
- **API 서버**: FastAPI (REST + WebSocket)
- **대시보드**: Next.js 14 (App Router) + Tailwind
- **DB**: SQLite (개발) / Postgres (프로덕션)
- **알림**: Telegram, Slack
- **모니터링**: Prometheus /metrics

## 전략

### 1. Satellite (5m 스캐너) - 비활성화
- 5분봉 돌파 + RVOL + VWAP
- BTC 레짐 필터
- Position State Machine (WEAK/NORMAL/STRONG/EXTREME)
- **상태**: OFF (Ignition으로 대체)

### 2. Attack (급등 추격) - v5.4
- **진입**: L1(70점) / L2(82점) / L3(88점)
- **비중**: L1(10%) / L2(30%) / L3(50%)
- **Candle Surge**: 1분봉 +5% / 5분봉 +3% (RVOL 조건 필수)
- **Anti-Chase**: 일일 +30% 이상 차단 (급등 감지 시 우회)
- 트레일링 스탑 + 타임스톱 45분

### 3. Surge (급등 감지) - v5.3 완화
- **진입 기준**: 1분봉 +4% 이상 (거래량 3배+)
- **손절**: -4% (여유있게, 기존 -1~2%)
- **트레일링**: 고점 -6% (되돌림 견딤, 기존 -1.5%)
- **익절 목표**: 3R (큰 수익 노림, 기존 1.5R)
- **타임스톱**: 15분 (충분한 시간, 기존 10분)
- **철학**: "작게 베팅하고 크게 먹는다" (승률 30~40% OK, 수익비 3:1)

### 4. Ignition (전조 패턴) - v4.2
- 급등 전조 패턴 감지 (Setup Engine)
- 점화(Ignition) 시점 진입
- Watchlist 관리 (최대 30종목)
- Surge 감지 후 추가 진입

### 5. Pullback (눌림목) - v4.2
- 급등 후 눌림목 매수
- VWAP 지지 확인
- 레벨별 진입 (L1/L2/L3)

### 6. Rebound Scalper (반등 스캘핑) - v4.2 신규
- **목적**: 급락 후 반등 구간 스캔
- **진입**: RSI 과매도 + 매수벽 확인 + 추세 필터
- **청산**: 2단계 익절 (TP1: +0.8% 50%, TP2: +1.5% 30%)
- **손절**: -1.2% (타이트)
- **타임스톱**: 30분
- **보호장치 3종**: 연속 손실 제한(3회) / 변동성 필터 / 추세 필터

### 7. Dip Scalper (급락 스캘핑) - v2.3 강화
- **목적**: 1분봉 급락 즉시 매수
- **진입**: ATR 기반 동적 임계값 (종목별 2~5% 급락)
- **청산**: TP +0.8% / SL -1.2% / 타임스톱 10분
- **v2.0 필터**: 거래량 급증(RVOL 2배+) + BTC 안정성 + 호가창 매수벽
- **v2.3 급등주 필터**: 5분봉 +7% 이상 급등주 차단 (펌프 앤 덤프 방지)
  - 급등 후 급락 = 바닥 모름, 연속 폭락 위험
  - "급락 스캘핑" ❌ "폭락의 희생양" ✅ 회피

## 리스크 관리 시스템 (v5.3)

### P0: 단일 진실 원장 (PositionLedger) + 포지션 복구
- 모든 포지션 상태의 단일 원장
- 체결 이벤트 기반 업데이트 (주문 제출 X, 체결 O)
- 거래소 잔고와 주기적 reconcile
- **v5.3 포지션 복구 시스템**:
  - DB 기반 전략별 포지션 복구 (PositionModel 테이블)
  - 서버 재시작 시 원래 전략 타입 유지 (DIP_SCALPER, ATTACK, etc.)
  - TP/SL 자동 재적용 (전략별 로직 복원)
  - 더 이상 모든 포지션이 SATELLITE로 잘못 분류되지 않음

### P2: 노출 한도 관리 (ExposureManager)
| 설정 | 기본값 | 설명 |
|------|--------|------|
| max_positions | 5 | 최대 동시 포지션 |
| max_total_exposure | 70% | 총 자본 대비 최대 노출 |
| max_symbol_exposure | 15% | 심볼당 최대 노출 |
| max_strategy_exposure | 40% | 전략당 최대 노출 |
| min_cash_reserve | 20% | 최소 현금 보유 |
| max_single_order | 10% | 단일 주문 최대 금액 |

### P3: 조건부 Time Stop (TimeStopPolicy)
- **핵심 변경**: R > 0 (수익 중)이면 Time Stop 안함
- R <= 0 이고 지정 시간 경과 시에만 청산
- 변동성 기반 시간 조절 (고변동성: 1.5배 연장)

### P1: 체결비용 로깅 (ExecutionCostLogger)
- fee_krw, slippage_bps, spread_bps 실측 기록
- 일별/심볼별/전략별 비용 분석

### P4: 유동성 필터 (UpbitLiquidityFilter)
| 설정 | 기본값 | 설명 |
|------|--------|------|
| min_volume_24h | 50억 | 최소 24시간 거래대금 |
| max_spread_bps | 10 | 최대 스프레드 (bps) |
| max_avg_spread_bps | 8 | 최대 평균 스프레드 (1시간) |
| depth_multiplier | 8x | 오더북 깊이 배수 |
| max_slippage | 0.2% | 최대 예상 슬리피지 |

### P5: 기대값 검증 (EdgeValidator)
- 최소 기대값: 0.3%
- 최소 Risk/Reward: 1.5
- 히스토리 기반 승률 검증 (최소 45%)

## 빠른 시작

### 1. 환경 설정

```bash
# 서버 설정
cp server/.env.example server/.env
# .env 파일에 Upbit API 키 입력

# 대시보드 설정
cp dashboard/.env.example dashboard/.env
```

### 2. 실행

```bash
# Python 서버 (포트 8086)
cd server
pip install -e .
python -m uvicorn src.app:app --reload --port 8086

# 별도 터미널에서 대시보드 (포트 3005)
cd dashboard
npm install
npm run dev -- --port 3005
```

### 3. Docker로 실행

```bash
# 개발 모드
docker-compose up -d

# 프로덕션 모드 (Postgres + Prometheus)
docker-compose -f docker-compose.prod.yml up -d
```

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | /api/health | 헬스체크 |
| GET | /api/mode | 현재 모드 (NORMAL/SAFE/HALT) |
| GET | /api/summary | 요약 정보 |
| GET | /api/positions | 포지션 목록 |
| GET | /api/orders | 주문 목록 |
| GET | /api/events | 이벤트 타임라인 |
| GET | /api/config | 설정 (readonly) |
| POST | /api/bot/pause | SAFE 모드 전환 |
| POST | /api/bot/resume | NORMAL 복귀 |
| POST | /api/bot/flatten | 긴급 포지션 정리 |

### WebSocket

```
WS /ws/stream
```
- 1-5초 주기로 summary/positions/events push

## 모드 설명

| 모드 | 설명 |
|------|------|
| NORMAL | 신규 진입 허용 |
| SAFE | 신규 진입 금지, 기존 포지션 축소 우선 |
| HALT | 긴급 청산만 |

### SAFE 트리거
- WS 지연/끊김 지속
- 주문 실패율 30% 초과
- 일손실 -1.5%

### HALT 트리거
- Reconcile 실패 (drift > 1%)
- 인증 오류 반복
- 일손실 -3.0%
- Hard DD (5%)

## 프로젝트 구조

```
/
├── server/                 # Python 백엔드
│   ├── src/
│   │   ├── api/           # FastAPI 라우터
│   │   ├── engine/        # 트레이딩 엔진
│   │   ├── exchange/      # 거래소 연결 (Upbit)
│   │   ├── strategies/    # 전략 (Satellite/Attack/Ignition/Pullback)
│   │   │   └── edge_validator.py  # P5: 기대값 검증
│   │   ├── risk/          # 리스크 관리
│   │   │   ├── exposure_manager.py    # P2: 노출 한도 관리
│   │   │   └── upbit_liquidity_filter.py  # P4: 유동성 필터
│   │   ├── position/      # 포지션 관리
│   │   │   └── time_stop_policy.py  # P3: 조건부 Time Stop
│   │   ├── portfolio/     # 포트폴리오 관리
│   │   │   ├── position_ledger.py     # P0: 단일 진실 원장
│   │   │   └── execution_cost_logger.py  # P1: 체결비용 로깅
│   │   └── monitoring/    # 알림/메트릭
│   └── tests/
├── dashboard/             # Next.js 프론트엔드
│   ├── app/              # App Router 페이지
│   ├── components/       # React 컴포넌트
│   └── lib/              # 유틸리티
└── docker-compose.yml
```

## 환경변수

### 필수

| 변수 | 설명 |
|------|------|
| UPBIT_API_KEY | Upbit API 키 |
| UPBIT_SECRET | Upbit Secret |

### 선택

| 변수 | 기본값 | 설명 |
|------|--------|------|
| ENABLE_LIVE_TRADING | false | Live 모드 활성화 |
| DATABASE_URL | sqlite... | DB 연결 문자열 |
| TELEGRAM_BOT_TOKEN | - | Telegram 봇 토큰 |
| TELEGRAM_CHAT_ID | - | Telegram 채팅 ID |
| SLACK_WEBHOOK_URL | - | Slack 웹훅 URL |
| DAILY_LOSS_LIMIT_SAFE | -0.015 | SAFE 전환 일손실 |
| DAILY_LOSS_LIMIT_HALT | -0.030 | HALT 전환 일손실 |

## 안전 수칙

1. **ENABLE_LIVE_TRADING=false**가 기본값입니다
2. API 키는 반드시 환경변수로만 관리
3. 로그에 시크릿이 노출되지 않도록 주의
4. Live 모드 전환 전 충분한 Paper 테스트 필수
5. 처음에는 소액으로 시작

## 테스트

```bash
cd server
pip install -e ".[dev]"
pytest tests/ -v
```

## 변경 이력

### v5.4 (2026-02-05) - 급등 감지 알고리즘 완화
- **Candle Surge 임계값 완화**: 조기 진입 기회 확대
  - 1분봉: 7% → 5% (RVOL 3x 유지)
  - 5분봉: 4% → 3% (RVOL 2x 유지)
- **Anti-Chase Gate 상향**: 20% → 30% (급등주 진입 허용 범위 확대)
- **예상 효과**: 일일 신호 2-3건 → 8-12건 증가
- **위험**: 거짓 신호 증가 가능 (모니터링 필요)

### v5.3 (2026-02-05) - 전략 완화 및 안정화
- **Surge 전략 완화**: 진입 1.5% → 4%, 손절 -1~2% → -4%, 트레일링 -1.5% → -6%, 익절 1.5R → 3R
- **Dip Scalper v2.3**: 급등주 필터 추가 (5분봉 +7% 차단) - 펌프 앤 덤프 방지
- **포지션 복구 시스템**: DB 기반 전략별 포지션 복구 (서버 재시작 시 원래 전략 유지)

### v5.2 (2024-12)
- **Attack 전략 활성화**: NORMAL 모드 (균형 조정)
- Attack Score L1 임계값 완화: 75 → 70 (하루 5-12건 예상)
- Attack Score L2/L3: 82/88점 (균형)

### v5.1 (2024-12)
- Candle Surge 감지 시 Anti-Chase Gate 우회
- 1분봉/5분봉 극단 급등 시 즉시 진입 허용

### v5.0 (2024-11)
- **분봉 급등 듀얼 트리거**: 1분봉 7% + 5분봉 4% 동시 감지
- P0: 단일 진실 원장 (PositionLedger) 도입
- P1: 체결비용 실측 로깅 (ExecutionCostLogger)
- P2: 노출 한도 관리 (ExposureManager) - "잔여 현금 0" 문제 해결
- P3: 조건부 Time Stop (TimeStopPolicy) - R > 0이면 안함
- P4: 업비트 유동성 필터 강화 (50억, 10bps)
- P5: 기대값 검증 (EdgeValidator) - 최소 edge 0.3%

### v4.2 (2024-10)
- **Rebound Scalper 전략 구현** (반등 스캘핑)
- 대시보드 Top 5 항상 표시 + Rebound 후보 추가
- 대시보드 상세 조건 항상 표시 + 한글화
- SurgeCandidates를 Attack Score 기반으로 변경

### v4.x (2024-09)
- Ignition 전략 추가
- Surge 감지 시스템
- Stop Watchdog (독립 손절 모니터링)

## 라이선스

MIT
