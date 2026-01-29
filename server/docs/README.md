# 트레이딩 시스템 문서

> Binance 자동 트레이딩 시스템 기술 문서

---

## 문서 목록

| 문서 | 설명 | 대상 |
|------|------|------|
| [ALGORITHM.md](./ALGORITHM.md) | 전체 알고리즘 구조 및 설계 | 개발자, 운영자 |
| [COMPONENTS.md](./COMPONENTS.md) | 컴포넌트별 클래스/메서드 상세 | 개발자 |
| [QUICK_REFERENCE.md](./QUICK_REFERENCE.md) | 운영 중 빠른 참조 가이드 | 운영자 |

---

## 시스템 개요

### 전략 구성
- **Core (70%)**: Cash & Carry 베이시스 차익
- **Satellite (20%)**: 모멘텀 스캐너
- **Reserve (10%)**: 긴급 마진용

### 핵심 원칙
1. **생존 우선**: 청산 0회 목표
2. **보수적 진입**: 엄격한 조건 충족 시에만
3. **분할 진입**: 3트랜치로 리스크 분산
4. **확인 진입**: 2단계 검증 후 진입
5. **다중 안전장치**: 15+ SAFE/HALT 트리거

---

## 빠른 시작

### 서버 실행
```bash
cd server
uvicorn src.app:app --port 8086
```

### 상태 확인
```bash
# 헬스 체크
curl http://localhost:8086/api/health

# 현재 모드
curl http://localhost:8086/api/mode

# 포지션
curl http://localhost:8086/api/positions
```

### 테스트 실행
```bash
pytest tests/test_algorithm_upgrade.py -v
```

---

## 모드 체계

```
NORMAL ──(트리거)──► SAFE ──(트리거)──► HALT
   ▲                  │
   └───(자동복구)─────┘      (수동 리셋 필요)
```

| 모드 | 신규 진입 | 청산 | 복구 방법 |
|------|----------|------|----------|
| NORMAL | ✅ | ✅ | - |
| SAFE | ❌ | ✅ | 자동 (조건 해제) |
| HALT | ❌ | ✅ | 수동 |

---

## 핵심 한도

| 항목 | 한도 |
|------|------|
| 일 손실 (SAFE) | -1.5% |
| 일 손실 (HALT) | -3.0% |
| 주 손실 | -5.0% |
| 총 노출 | 120% |
| Core 심볼당 | 10% |
| Satellite 심볼당 | 3% |

---

## 파일 구조

```
server/
├── docs/
│   ├── README.md           # 이 파일
│   ├── ALGORITHM.md        # 알고리즘 상세
│   ├── COMPONENTS.md       # 컴포넌트 상세
│   └── QUICK_REFERENCE.md  # 빠른 참조
│
├── src/
│   ├── config.py           # 설정
│   ├── app.py              # API 서버
│   ├── data/               # 데이터 수집
│   ├── features/           # 피처 엔진
│   ├── strategies/         # 전략
│   ├── risk/               # 리스크 관리
│   ├── execution/          # 실행 엔진
│   ├── portfolio/          # 포트폴리오
│   ├── exchange/           # 거래소 연결
│   └── monitoring/         # 모니터링
│
├── tests/                  # 테스트
└── .env                    # 환경 변수
```

---

## 연락처

문제 발생 시 Slack `#trading-alerts` 채널 확인

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2026-01-29 | 2.0 | 생존 중심 업그레이드 |
