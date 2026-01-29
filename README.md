# Binance 자동매매 시스템

Binance Spot + USD-M Perp 자동매매 시스템 + FastAPI + Next.js 대시보드

## 기술 스택

- **Trading Engine**: Python 3.11+ (asyncio)
- **API 서버**: FastAPI (REST + WebSocket)
- **대시보드**: Next.js 14 (App Router) + Tailwind
- **DB**: SQLite (개발) / Postgres (프로덕션)
- **알림**: Telegram
- **모니터링**: Prometheus /metrics

## 전략

### Core (캐시앤캐리)
- 현물-무기한선물 시장중립 전략
- 펀딩비 수익 추구
- Edge % 기반 진입/청산

### Satellite (5m 스캐너)
- 5분봉 돌파 + RVOL + VWAP
- BTC 1h 레짐 필터
- 하드 손절 / 트레일링 스탑 / 타임스톱

## 빠른 시작

### 1. 환경 설정

```bash
# 서버 설정
cp server/.env.example server/.env
# .env 파일에 API 키 입력

# 대시보드 설정
cp dashboard/.env.example dashboard/.env
```

### 2. Paper 모드 실행 (개발)

```bash
# Python 서버
cd server
pip install -e .
python -m uvicorn src.app:app --reload

# 별도 터미널에서 대시보드
cd dashboard
npm install
npm run dev
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
| SAFE | 신규 진입 금지, 기존 포지션 축소/헤지 우선 |
| HALT | 긴급 청산만 |

### SAFE 트리거
- WS 지연/끊김 지속
- 주문 실패율 30% 초과
- 한쪽 체결 후 헤지 실패
- 일손실 -1.5%

### HALT 트리거
- Reconcile 실패 (drift > 1%)
- 인증 오류 반복
- 일손실 -3.0%
- Liquidation distance 임계 미만

## 안전 수칙

1. **ENABLE_LIVE_TRADING=false**가 기본값입니다
2. API 키는 반드시 환경변수로만 관리
3. 로그에 시크릿이 노출되지 않도록 주의
4. Live 모드 전환 전 충분한 Paper 테스트 필수
5. 처음에는 소액으로 시작

## Live 모드 활성화

```bash
# .env 파일에서
ENABLE_LIVE_TRADING=true

# 또는 환경변수로
export ENABLE_LIVE_TRADING=true
```

**주의**: Live 모드에서는 실제 자금이 거래됩니다!

## 테스트

```bash
cd server
pip install -e ".[dev]"
pytest tests/ -v
```

## 프로젝트 구조

```
/
├── server/                 # Python 백엔드
│   ├── src/
│   │   ├── api/           # FastAPI 라우터
│   │   ├── engine/        # 트레이딩 엔진
│   │   ├── exchange/      # 거래소 연결
│   │   ├── strategies/    # 전략 (Core/Satellite)
│   │   ├── risk/          # 리스크 관리
│   │   ├── execution/     # 주문 실행
│   │   ├── portfolio/     # 포트폴리오 관리
│   │   └── monitoring/    # 알림/메트릭
│   └── tests/
├── dashboard/             # Next.js 프론트엔드
│   ├── app/              # App Router 페이지
│   ├── components/       # React 컴포넌트
│   └── lib/              # 유틸리티
├── docker-compose.yml    # 개발용
└── docker-compose.prod.yml # 프로덕션용
```

## 환경변수

### 필수

| 변수 | 설명 |
|------|------|
| BINANCE_TESTNET_API_KEY | Testnet API 키 |
| BINANCE_TESTNET_SECRET | Testnet Secret |
| BINANCE_API_KEY | Live API 키 |
| BINANCE_SECRET | Live Secret |

### 선택

| 변수 | 기본값 | 설명 |
|------|--------|------|
| ENABLE_LIVE_TRADING | false | Live 모드 활성화 |
| DATABASE_URL | sqlite... | DB 연결 문자열 |
| TELEGRAM_BOT_TOKEN | - | Telegram 봇 토큰 |
| TELEGRAM_CHAT_ID | - | Telegram 채팅 ID |
| DAILY_LOSS_LIMIT_SAFE | -0.015 | SAFE 전환 일손실 |
| DAILY_LOSS_LIMIT_HALT | -0.030 | HALT 전환 일손실 |

## 라이선스

MIT
