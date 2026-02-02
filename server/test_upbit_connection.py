"""Upbit API 연결 테스트 스크립트"""

import asyncio
import sys
sys.path.insert(0, '.')

from src.exchange.upbit import UpbitExchange
from src.config import get_settings


async def test_upbit_connection():
    """Upbit API 연결 테스트"""
    settings = get_settings()

    print("=" * 60)
    print("Upbit API 연결 테스트")
    print("=" * 60)
    print(f"Exchange Type: {settings.exchange_type}")
    print(f"Is Upbit: {settings.is_upbit}")
    print()

    # Upbit Exchange 초기화
    exchange = UpbitExchange(
        api_key=settings.upbit_api_key,
        secret=settings.upbit_secret,
    )

    try:
        # 1. 연결 테스트
        print("[1] 거래소 연결 중...")
        await exchange.connect()
        print(f"    연결 상태: {'성공' if exchange.is_connected else '실패'}")
        print()

        # 2. 잔고 조회
        print("[2] KRW 잔고 조회...")
        krw_balance = await exchange.get_balance("KRW")
        if krw_balance:
            print(f"    KRW 잔고:")
            print(f"      - 총액: {krw_balance.total:,.0f} KRW")
            print(f"      - 가용: {krw_balance.free:,.0f} KRW")
            print(f"      - 동결: {krw_balance.locked:,.0f} KRW")
        else:
            print("    잔고 조회 실패")
        print()

        # 3. 전체 잔고 조회
        print("[3] 전체 자산 조회...")
        all_balances = await exchange.get_all_balances()
        if all_balances:
            print(f"    보유 자산 수: {len(all_balances)}개")
            for b in all_balances:
                # BalanceInfo: asset, total, free, locked
                if b.total > 0 or b.locked > 0:
                    print(f"      - {b.asset}: {b.total:.8f} (가용: {b.free:.8f}, 동결: {b.locked:.8f})")
        print()

        # 4. 시세 조회 (BTC)
        print("[4] BTC 시세 조회...")
        btc_ticker = await exchange.get_ticker("KRW-BTC")
        if btc_ticker:
            print(f"    KRW-BTC:")
            print(f"      - 현재가: {btc_ticker.last:,.0f} KRW")
            print(f"      - 매수호가: {btc_ticker.bid:,.0f} KRW")
            print(f"      - 매도호가: {btc_ticker.ask:,.0f} KRW")
            print(f"      - 거래대금(24h): {btc_ticker.volume_24h/1e9:,.1f}억 KRW")
        print()

        # 5. 마켓 목록 조회
        print("[5] KRW 마켓 목록 조회...")
        markets = await exchange.get_markets()
        krw_markets = [m for m in markets if m.get("market", "").startswith("KRW-")]
        print(f"    KRW 마켓 수: {len(krw_markets)}개")
        print(f"    상위 5개: {[m['market'] for m in krw_markets[:5]]}")
        print()

        # 6. 전체 시세 조회
        print("[6] 전체 시세 조회 (상위 10개 거래대금)...")
        all_tickers = await exchange.get_all_tickers()
        # 거래대금 순 정렬
        sorted_tickers = sorted(
            all_tickers.items(),
            key=lambda x: x[1].get("acc_trade_price_24h", 0),
            reverse=True
        )[:10]

        for symbol, ticker in sorted_tickers:
            price = ticker.get("trade_price", 0)
            change = ticker.get("signed_change_rate", 0) * 100
            volume = ticker.get("acc_trade_price_24h", 0) / 1e9
            print(f"    {symbol}: {price:,.0f} KRW ({change:+.2f}%) 거래대금: {volume:,.1f}억")
        print()

        # 7. 호가 조회 (BTC)
        print("[7] BTC 호가 조회...")
        orderbook = await exchange.get_orderbook("KRW-BTC")
        if orderbook:
            asks = orderbook.get("orderbook_units", [])[:3]
            bids = orderbook.get("orderbook_units", [])[:3]
            print("    매도 호가 (상위 3개):")
            for ask in asks:
                print(f"      {ask.get('ask_price', 0):,.0f} KRW x {ask.get('ask_size', 0):.4f}")
            print("    매수 호가 (상위 3개):")
            for bid in bids:
                print(f"      {bid.get('bid_price', 0):,.0f} KRW x {bid.get('bid_size', 0):.4f}")

            # 스프레드 계산
            if asks and bids:
                best_ask = asks[0].get("ask_price", 0)
                best_bid = bids[0].get("bid_price", 0)
                spread_pct = (best_ask - best_bid) / best_bid * 100 if best_bid else 0
                print(f"    스프레드: {spread_pct:.4f}%")
        print()

        print("=" * 60)
        print("테스트 완료!")
        print("=" * 60)

    except Exception as e:
        print(f"에러 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await exchange.disconnect()


if __name__ == "__main__":
    asyncio.run(test_upbit_connection())
