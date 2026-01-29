"""Binance Spot Testnet 연동 테스트"""

import asyncio
from src.config import get_settings
from src.exchange.binance_spot import BinanceSpotExchange


async def test_spot_connection():
    settings = get_settings()

    print("=" * 50)
    print("Binance SPOT Testnet 연동 테스트")
    print("=" * 50)
    print(f"Mode: {'Paper (Testnet)' if settings.is_paper_mode else 'LIVE'}")
    print()

    # Spot Testnet 연결 테스트
    print("[1] Spot Testnet 연결 테스트...")
    spot = BinanceSpotExchange(
        api_key=settings.active_api_key,
        secret=settings.active_secret,
        testnet=True,
    )

    connected = await spot.connect()
    if connected:
        print("    ✅ 연결 성공!")
    else:
        print("    ❌ 연결 실패")
        return

    # 시세 조회
    print("\n[2] BTCUSDT 시세 조회...")
    ticker = await spot.get_ticker("BTCUSDT")
    if ticker:
        print(f"    ✅ Bid: ${ticker.bid:,.2f}")
        print(f"    ✅ Ask: ${ticker.ask:,.2f}")
        print(f"    ✅ Last: ${ticker.last:,.2f}")
    else:
        print("    ❌ 시세 조회 실패")

    # 잔고 조회
    print("\n[3] 잔고 조회...")
    balance = await spot.get_balance("USDT")
    if balance:
        print(f"    ✅ USDT Total: ${balance.total:,.2f}")
        print(f"    ✅ USDT Free: ${balance.free:,.2f}")
    else:
        print("    ❌ 잔고 조회 실패")

    # 모든 잔고 조회
    print("\n[4] 전체 잔고 조회...")
    all_balances = await spot.get_all_balances()
    if all_balances:
        for bal in all_balances[:5]:  # 상위 5개만
            print(f"    💰 {bal.asset}: {bal.total:,.4f}")
    else:
        print("    ℹ️  잔고 없음")

    # 미체결 주문 조회
    print("\n[5] 미체결 주문 조회...")
    orders = await spot.get_open_orders()
    if orders:
        for order in orders[:3]:
            print(f"    📋 {order}")
    else:
        print("    ℹ️  미체결 주문 없음")

    # 연결 해제
    await spot.disconnect()
    print("\n" + "=" * 50)
    print("Spot Testnet 테스트 완료!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_spot_connection())
