#!/usr/bin/env python3
"""
Surge 모니터링 스크립트 (30분)
- 매 5초마다 상태 체크
- 신호 발생/차단 사유 기록
- 필터 통계 변화 추적
"""
import asyncio
import json
from datetime import datetime, timedelta
import httpx

BASE_URL = "http://localhost:8086"
MONITOR_DURATION = 30 * 60  # 30분
CHECK_INTERVAL = 5  # 5초마다

# 이전 상태 저장
prev_stats = {}
prev_signals = set()
events = []


async def check_status(client):
    """현재 상태 체크"""
    try:
        r = await client.get(f"{BASE_URL}/api/surge/status", timeout=5)
        return r.json()
    except Exception as e:
        return None


async def check_market_data(client):
    """상위 코인 시장 데이터 (변화율, 거래량)"""
    try:
        r = await client.get(f"{BASE_URL}/api/market/tickers", timeout=5)
        data = r.json()
        # 1분 변화율 기준 상위 10개
        if isinstance(data, list):
            sorted_data = sorted(data, key=lambda x: x.get("change_rate", 0) or 0, reverse=True)
            return sorted_data[:10]
        return []
    except:
        return []


def analyze_changes(current_stats, current_signals):
    """상태 변화 분석"""
    global prev_stats, prev_signals

    changes = []

    # 필터 통계 변화
    for name, stats in current_stats.items():
        if name not in prev_stats:
            continue

        prev = prev_stats[name]

        # 새로운 차단 발생
        blocked_diff = stats.get("blocked", 0) - prev.get("blocked", 0)
        if blocked_diff > 0:
            changes.append(f"[차단] {name}: +{blocked_diff}회")

        # 새로운 통과 발생
        passed_diff = stats.get("passed", 0) - prev.get("passed", 0)
        if passed_diff > 0 and name != "hot_yesterday_policy":
            changes.append(f"[통과] {name}: +{passed_diff}회")

        # structure_anti_chase 상세
        if name == "structure_anti_chase":
            a_diff = stats.get("type_a_passed", 0) - prev.get("type_a_passed", 0)
            b_diff = stats.get("type_b_passed", 0) - prev.get("type_b_passed", 0)
            if a_diff > 0:
                changes.append(f"[신호] Type A (첫 점화): +{a_diff}회")
            if b_diff > 0:
                changes.append(f"[신호] Type B (리테스트): +{b_diff}회")

    # 새 신호 발생
    current_signal_symbols = {s.get("symbol") for s in current_signals if s}
    new_signals = current_signal_symbols - prev_signals
    if new_signals:
        for sym in new_signals:
            signal = next((s for s in current_signals if s.get("symbol") == sym), {})
            changes.append(f"[매수신호] {sym} @ {signal.get('price', 'N/A')}")

    prev_stats = {k: dict(v) for k, v in current_stats.items()}
    prev_signals = current_signal_symbols

    return changes


async def monitor():
    print("=" * 70)
    print(f"급등 모니터링 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"모니터링 시간: 30분")
    print("=" * 70)
    print()

    start_time = datetime.now()
    end_time = start_time + timedelta(seconds=MONITOR_DURATION)

    global prev_stats, prev_signals

    async with httpx.AsyncClient() as client:
        # 초기 상태 저장
        status = await check_status(client)
        if status:
            prev_stats = {k: dict(v) for k, v in status.get("filter_stats", {}).items()}
            prev_signals = {s.get("symbol") for s in status.get("signals", []) if s}

        check_count = 0

        while datetime.now() < end_time:
            check_count += 1
            elapsed = (datetime.now() - start_time).seconds
            remaining = MONITOR_DURATION - elapsed

            # 상태 체크
            status = await check_status(client)
            if not status:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 서버 연결 실패")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # 변화 분석
            changes = analyze_changes(
                status.get("filter_stats", {}),
                status.get("signals", [])
            )

            # 시장 데이터 (1분마다만)
            if check_count % 12 == 1:  # 60초마다
                top_movers = await check_market_data(client)
                if top_movers:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] === 상위 급등 코인 ===")
                    for i, coin in enumerate(top_movers[:5], 1):
                        symbol = coin.get("symbol", "?")
                        change = coin.get("change_rate", 0) or 0
                        vol = coin.get("acc_trade_price_24h", 0) or 0
                        print(f"  {i}. {symbol}: {change*100:+.2f}% (거래대금: {vol/1e8:.0f}억)")

                    # 신호 상태
                    active = status.get("active_signals", 0)
                    positions = status.get("active_positions", 0)
                    print(f"  → 활성 신호: {active}개, 포지션: {positions}개")

                    # 필터 통계
                    fs = status.get("filter_stats", {})
                    vol_guard = fs.get("vol_overheat_guard", {})
                    anti_chase = fs.get("structure_anti_chase", {})
                    print(f"  → Vol Guard: {vol_guard.get('total_checks', 0)}회 중 {vol_guard.get('blocked', 0)}회 차단")
                    print(f"  → Anti-Chase: Type A {anti_chase.get('type_a_passed', 0)}회, Type B {anti_chase.get('type_b_passed', 0)}회, 차단 {anti_chase.get('blocked', 0)}회")

            # 변화가 있으면 출력
            if changes:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 변화 감지:")
                for change in changes:
                    print(f"  {change}")
                    events.append((datetime.now(), change))

            # 남은 시간 표시 (5분마다)
            if check_count % 60 == 0:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 남은 시간: {remaining//60}분 {remaining%60}초")

            await asyncio.sleep(CHECK_INTERVAL)

    # 요약
    print("\n" + "=" * 70)
    print("모니터링 종료 - 요약")
    print("=" * 70)

    if events:
        print(f"\n총 {len(events)}개 이벤트 발생:")
        for time, event in events[-20:]:  # 최근 20개만
            print(f"  [{time.strftime('%H:%M:%S')}] {event}")
    else:
        print("\n이벤트 없음 (신호 발생/차단 없음)")

    # 최종 통계
    status = await check_status(client)
    if status:
        print("\n[최종 필터 통계]")
        for name, stats in status.get("filter_stats", {}).items():
            print(f"  {name}:")
            for k, v in stats.items():
                print(f"    {k}: {v}")


if __name__ == "__main__":
    asyncio.run(monitor())
