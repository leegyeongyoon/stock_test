"""Binance 연동 테스트 스크립트"""

import asyncio
from src.config import get_settings
from src.exchange.binance_spot import BinanceSpotExchange
from src.exchange.binance_perp import BinancePerpExchange


async def test_connection():
    settings = get_settings()

    print("=" * 50)
    print("Binance 연동 테스트")
    print("=" * 50)
    print(f"Mode: {'Paper (Testnet)' if settings.is_paper_mode else 'LIVE'}")
    print()

    # Futures Testnet 연결 테스트
    print("[1] Futures Testnet 연결 테스트...")
    perp = BinancePerpExchange(
        api_key=settings.active_api_key,
        secret=settings.active_secret,
        testnet=True,
    )

    connected = await perp.connect()
    if connected:
        print("    ✅ 연결 성공!")
    else:
        print("    ❌ 연결 실패")
        return

    # 시세 조회
    print("\n[2] BTCUSDT 시세 조회...")
    ticker = await perp.get_ticker("BTCUSDT")
    if ticker:
        print(f"    ✅ Bid: ${ticker.bid:,.2f}")
        print(f"    ✅ Ask: ${ticker.ask:,.2f}")
        print(f"    ✅ Last: ${ticker.last:,.2f}")
    else:
        print("    ❌ 시세 조회 실패")

    # 잔고 조회
    print("\n[3] USDT 잔고 조회...")
    balance = await perp.get_balance("USDT")
    if balance:
        print(f"    ✅ Total: ${balance.total:,.2f}")
        print(f"    ✅ Free: ${balance.free:,.2f}")
        print(f"    ✅ Locked: ${balance.locked:,.2f}")
    else:
        print("    ❌ 잔고 조회 실패")

    # 펀딩비 조회
    print("\n[4] BTCUSDT 펀딩비 조회...")
    funding = await perp.get_funding_rate("BTCUSDT")
    if funding is not None:
        print(f"    ✅ Funding Rate: {funding:.6%}")
    else:
        print("    ❌ 펀딩비 조회 실패")

    # 포지션 조회
    print("\n[5] 현재 포지션 조회...")
    positions = await perp.get_positions()
    if positions:
        for pos in positions:
            print(f"    📊 {pos.symbol}: {pos.side} {pos.quantity} @ ${pos.entry_price:,.2f}")
    else:
        print("    ℹ️  열린 포지션 없음")

    # 연결 해제
    await perp.disconnect()
    print("\n" + "=" * 50)
    print("테스트 완료!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_connection())
