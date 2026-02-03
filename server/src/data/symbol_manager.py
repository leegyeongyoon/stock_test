"""Symbol Manager - 동적 심볼 관리

Binance/Upbit 심볼을 동적으로 관리:
- exchangeInfo에서 전체 심볼 조회
- 유동성 필터 적용 (거래대금, 스프레드)
- 정밀도 정보 캐싱
- 주기적 갱신 (1시간마다)
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# 유동성 필터 기준
MIN_VOLUME_24H = 50_000_000  # 50M USDT (Binance)
MIN_VOLUME_24H_KRW = 1_000_000_000  # 10억 KRW (Upbit)
MAX_SPREAD_BPS = 10  # 10 basis points
MIN_NOTIONAL = 100  # 100 USDT 최소 주문 (Binance)
MIN_NOTIONAL_KRW = 5000  # 5000 KRW 최소 주문 (Upbit)

# 갱신 주기
REFRESH_INTERVAL_MINUTES = 60


@dataclass
class SymbolInfo:
    """심볼 정보"""

    symbol: str  # BTCUSDT
    base_asset: str  # BTC
    quote_asset: str  # USDT
    price_precision: int  # 가격 소수점 자릿수
    quantity_precision: int  # 수량 소수점 자릿수
    min_notional: float  # 최소 주문금액
    step_size: float  # 수량 단위
    tick_size: float  # 가격 단위
    min_qty: float  # 최소 수량
    volume_24h: float = 0.0  # 24h 거래대금
    spread_bps: float = 0.0  # 스프레드 (basis points)
    last_price: float = 0.0  # 최근 가격
    funding_rate: float = 0.0  # 펀딩비
    korean_name: str = ""  # 한글명 (Upbit)
    english_name: str = ""  # 영문명 (Upbit)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "base_asset": self.base_asset,
            "price_precision": self.price_precision,
            "quantity_precision": self.quantity_precision,
            "min_notional": self.min_notional,
            "step_size": self.step_size,
            "tick_size": self.tick_size,
            "min_qty": self.min_qty,
            "volume_24h": self.volume_24h,
            "spread_bps": self.spread_bps,
            "last_price": self.last_price,
            "funding_rate": self.funding_rate,
            "korean_name": self.korean_name,
            "english_name": self.english_name,
        }


class SymbolManager:
    """
    동적 심볼 관리자

    책임:
    1. Binance/Upbit 심볼 조회
    2. 유동성 필터링 (거래대금, 스프레드)
    3. 심볼 정보 캐싱 (정밀도 등)
    4. 주기적 갱신
    """

    def __init__(
        self,
        perp_exchange=None,
        spot_exchange=None,
        upbit_exchange=None,
        min_volume_24h: float = MIN_VOLUME_24H,
        min_volume_24h_krw: float = MIN_VOLUME_24H_KRW,
        max_spread_bps: float = MAX_SPREAD_BPS,
    ):
        self.perp_exchange = perp_exchange
        self.spot_exchange = spot_exchange
        self.upbit_exchange = upbit_exchange
        self.min_volume_24h = min_volume_24h
        self.min_volume_24h_krw = min_volume_24h_krw
        self.max_spread_bps = max_spread_bps

        # 거래소 타입 결정
        self._is_upbit = upbit_exchange is not None

        # 심볼 캐시
        self._symbols: dict[str, SymbolInfo] = {}
        self._qualified: list[str] = []  # 유동성 통과 심볼
        self._last_refresh: Optional[datetime] = None

        # 통계
        self._total_symbols: int = 0  # 전체 심볼 수
        self._filtered_count: int = 0  # 필터 통과 심볼 수

    def needs_refresh(self) -> bool:
        """갱신 필요 여부"""
        if self._last_refresh is None:
            return True

        elapsed = datetime.utcnow() - self._last_refresh
        return elapsed > timedelta(minutes=REFRESH_INTERVAL_MINUTES)

    async def refresh(self) -> list[str]:
        """
        심볼 목록 갱신

        1. exchangeInfo에서 전체 심볼 조회
        2. 24h 티커에서 거래대금 조회
        3. 호가에서 스프레드 계산
        4. 유동성 필터 적용
        5. 결과 캐싱

        Returns:
            유동성 통과 심볼 목록
        """
        if self._is_upbit:
            return await self._refresh_upbit()
        else:
            return await self._refresh_binance()

    async def _refresh_upbit(self) -> list[str]:
        """Upbit 심볼 목록 갱신"""
        if not self.upbit_exchange:
            logger.error("SymbolManager: upbit_exchange not set")
            return self._qualified

        try:
            # 1. KRW 마켓 목록 조회
            markets = await self.upbit_exchange.get_markets()
            krw_markets = [m for m in markets if m.get("market", "").startswith("KRW-")]

            self._total_symbols = len(krw_markets)

            # 2. 전체 시세 조회
            market_codes = [m["market"] for m in krw_markets]
            tickers = await self.upbit_exchange.get_all_tickers(market_codes)

            # 3. 심볼 정보 생성 및 필터링
            self._symbols = {}
            self._qualified = []

            for market in krw_markets:
                symbol = market["market"]  # KRW-BTC
                base_asset = symbol.replace("KRW-", "")

                ticker = tickers.get(symbol, {})
                volume_24h = ticker.get("acc_trade_price_24h", 0)

                # 한글명, 영문명 추출
                korean_name = market.get("korean_name", "")
                english_name = market.get("english_name", "")

                # Upbit은 정밀도 정보가 제한적 - 기본값 사용
                info = SymbolInfo(
                    symbol=symbol,
                    base_asset=base_asset,
                    quote_asset="KRW",
                    price_precision=0,  # KRW는 정수
                    quantity_precision=8,  # 대부분 코인은 8자리
                    min_notional=MIN_NOTIONAL_KRW,
                    step_size=0.00000001,
                    tick_size=1.0,  # KRW 단위
                    min_qty=0.00000001,
                    volume_24h=volume_24h,
                    spread_bps=0,  # Upbit은 스프레드 계산 생략
                    last_price=ticker.get("trade_price", 0),
                    funding_rate=0,  # Upbit은 펀딩비 없음
                    korean_name=korean_name,
                    english_name=english_name,
                )

                self._symbols[symbol] = info

                # 유동성 필터 (거래대금만)
                if volume_24h >= self.min_volume_24h_krw:
                    self._qualified.append(symbol)

            # 거래대금 순으로 정렬 (BTC, ETH가 상위에 오도록)
            self._qualified.sort(
                key=lambda s: self._symbols[s].volume_24h,
                reverse=True,
            )

            self._filtered_count = len(self._qualified)
            self._last_refresh = datetime.utcnow()

            logger.info(
                "SymbolManager: Refreshed Upbit symbols",
                total=self._total_symbols,
                qualified=self._filtered_count,
                min_volume=f"{self.min_volume_24h_krw/1e9:.1f}B KRW",
            )

            return self._qualified

        except Exception as e:
            logger.error("SymbolManager: Upbit refresh failed", error=str(e))
            return self._qualified

    async def _refresh_binance(self) -> list[str]:
        """Binance 심볼 목록 갱신"""
        if not self.perp_exchange:
            logger.error("SymbolManager: perp_exchange not set")
            return self._qualified

        try:
            # 1. 거래소 정보에서 심볼 + 정밀도 가져오기
            exchange_info = await self.perp_exchange.get_exchange_info()
            if not exchange_info:
                logger.error("SymbolManager: Failed to get exchange info")
                return self._qualified

            # 심볼 정보 파싱
            symbol_rules = self._parse_exchange_info(exchange_info)
            self._total_symbols = len(symbol_rules)

            # 2. 24h 티커 일괄 조회
            tickers = await self.perp_exchange.get_all_tickers()

            # 3. 호가 일괄 조회 (스프레드 계산용)
            book_tickers = await self.perp_exchange.get_all_book_tickers()

            # 4. 펀딩비 일괄 조회
            funding_rates = await self.perp_exchange.get_all_funding_rates()

            # 5. 심볼 정보 통합 및 필터링
            self._symbols = {}
            self._qualified = []

            for symbol, rules in symbol_rules.items():
                ticker = tickers.get(symbol, {})
                book = book_tickers.get(symbol, {})

                # 거래대금
                volume_24h = ticker.get("quoteVolume", 0)

                # 스프레드 계산
                bid = book.get("bid", 0)
                ask = book.get("ask", 0)
                mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                spread_bps = ((ask - bid) / mid_price * 10000) if mid_price > 0 else 999

                # SymbolInfo 생성
                info = SymbolInfo(
                    symbol=symbol,
                    base_asset=rules["base_asset"],
                    quote_asset=rules["quote_asset"],
                    price_precision=rules["price_precision"],
                    quantity_precision=rules["quantity_precision"],
                    min_notional=rules["min_notional"],
                    step_size=rules["step_size"],
                    tick_size=rules["tick_size"],
                    min_qty=rules["min_qty"],
                    volume_24h=volume_24h,
                    spread_bps=spread_bps,
                    last_price=ticker.get("last", 0),
                    funding_rate=funding_rates.get(symbol, 0),
                )

                self._symbols[symbol] = info

                # 유동성 필터
                if self._passes_liquidity_filter(info):
                    self._qualified.append(symbol)

            self._filtered_count = len(self._qualified)
            self._last_refresh = datetime.utcnow()

            logger.info(
                "SymbolManager: Refreshed symbols",
                total=self._total_symbols,
                qualified=self._filtered_count,
                min_volume=f"{self.min_volume_24h/1e6:.0f}M",
                max_spread=f"{self.max_spread_bps}bps",
            )

            return self._qualified

        except Exception as e:
            logger.error("SymbolManager: Refresh failed", error=str(e))
            return self._qualified

    def _parse_exchange_info(self, exchange_info: dict) -> dict[str, dict]:
        """exchangeInfo에서 심볼 정보 파싱"""
        result = {}

        for symbol_info in exchange_info.get("symbols", []):
            # USDT 페어 + TRADING + PERPETUAL만
            if (
                symbol_info.get("quoteAsset") != "USDT"
                or symbol_info.get("status") != "TRADING"
                or symbol_info.get("contractType") != "PERPETUAL"
            ):
                continue

            symbol = symbol_info["symbol"]

            # 필터에서 정밀도 정보 추출
            price_precision = symbol_info.get("pricePrecision", 2)
            quantity_precision = symbol_info.get("quantityPrecision", 3)

            step_size = 0.001
            tick_size = 0.01
            min_qty = 0.001
            min_notional = MIN_NOTIONAL

            for f in symbol_info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0.001))
                    min_qty = float(f.get("minQty", 0.001))
                elif f["filterType"] == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0.01))
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 100))

            result[symbol] = {
                "base_asset": symbol_info.get("baseAsset", ""),
                "quote_asset": symbol_info.get("quoteAsset", "USDT"),
                "price_precision": price_precision,
                "quantity_precision": quantity_precision,
                "step_size": step_size,
                "tick_size": tick_size,
                "min_qty": min_qty,
                "min_notional": min_notional,
            }

        return result

    def _passes_liquidity_filter(self, info: SymbolInfo) -> bool:
        """유동성 필터 통과 여부"""
        # 거래소별 거래대금 기준
        if self._is_upbit:
            min_volume = self.min_volume_24h_krw
        else:
            min_volume = self.min_volume_24h

        # 거래대금 체크
        if info.volume_24h < min_volume:
            return False

        # 스프레드 체크 (Upbit은 스킵)
        if not self._is_upbit and info.spread_bps > self.max_spread_bps:
            return False

        return True

    def get_qualified_symbols(self) -> list[str]:
        """유동성 통과 심볼 목록"""
        return self._qualified.copy()

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """심볼 정보 조회"""
        return self._symbols.get(symbol)

    def is_qualified(self, symbol: str) -> bool:
        """유동성 통과 여부"""
        return symbol in self._qualified

    def get_all_symbols(self) -> list[str]:
        """전체 심볼 목록 (필터 적용 안 됨)"""
        return list(self._symbols.keys())

    def round_quantity(self, symbol: str, quantity: float) -> float:
        """
        심볼에 맞게 수량 정밀도 처리

        Args:
            symbol: 심볼
            quantity: 원래 수량

        Returns:
            정밀도 맞춘 수량 (step_size 단위로 내림)
        """
        info = self._symbols.get(symbol)
        if not info:
            # Upbit 심볼이면 기본값 사용
            if self._is_upbit and symbol.startswith("KRW-"):
                # Upbit 기본 정밀도: 8자리
                rounded = math.floor(quantity * 1e8) / 1e8
                return rounded if rounded > 0.00000001 else 0
            logger.warning(f"SymbolManager: Unknown symbol {symbol}")
            return 0

        # step_size 단위로 내림
        step = info.step_size
        if step <= 0:
            step = 0.00000001 if self._is_upbit else 0.001

        rounded = math.floor(quantity / step) * step

        # 정밀도 맞추기
        rounded = round(rounded, info.quantity_precision)

        # 최소 수량 체크
        if rounded < info.min_qty:
            return 0

        return rounded

    def round_price(self, symbol: str, price: float) -> float:
        """
        심볼에 맞게 가격 정밀도 처리

        Args:
            symbol: 심볼
            price: 원래 가격

        Returns:
            정밀도 맞춘 가격
        """
        info = self._symbols.get(symbol)
        if not info:
            return price

        # tick_size 단위로 반올림
        tick = info.tick_size
        if tick <= 0:
            tick = 0.01

        rounded = round(price / tick) * tick
        return round(rounded, info.price_precision)

    def get_min_notional(self, symbol: str) -> float:
        """심볼의 최소 주문금액"""
        info = self._symbols.get(symbol)
        if info:
            return info.min_notional
        return MIN_NOTIONAL_KRW if self._is_upbit else MIN_NOTIONAL

    def get_korean_name(self, symbol: str) -> str:
        """심볼의 한글명"""
        info = self._symbols.get(symbol)
        return info.korean_name if info else ""

    def get_symbol_names(self, symbol: str) -> dict:
        """심볼의 이름 정보 (한글명, 영문명, base_asset)"""
        info = self._symbols.get(symbol)
        if info:
            return {
                "base_asset": info.base_asset,
                "korean_name": info.korean_name,
                "english_name": info.english_name,
            }
        return {
            "base_asset": symbol.replace("KRW-", ""),
            "korean_name": "",
            "english_name": "",
        }

    def get_stats(self) -> dict:
        """통계 정보"""
        return {
            "total_symbols": self._total_symbols,
            "qualified_symbols": self._filtered_count,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "filter_criteria": {
                "min_volume_24h": self.min_volume_24h,
                "max_spread_bps": self.max_spread_bps,
            },
        }

    def get_top_volume_symbols(self, limit: int = 20) -> list[dict]:
        """거래대금 상위 심볼 목록"""
        sorted_symbols = sorted(
            self._symbols.values(),
            key=lambda x: x.volume_24h,
            reverse=True,
        )

        return [s.to_dict() for s in sorted_symbols[:limit]]


# 싱글톤
_symbol_manager: Optional[SymbolManager] = None


def get_symbol_manager() -> SymbolManager:
    """SymbolManager 싱글톤"""
    global _symbol_manager
    if _symbol_manager is None:
        _symbol_manager = SymbolManager()
    return _symbol_manager


def init_symbol_manager(
    perp_exchange=None,
    spot_exchange=None,
    upbit_exchange=None,
    min_volume_24h: float = MIN_VOLUME_24H,
    min_volume_24h_krw: float = MIN_VOLUME_24H_KRW,
    max_spread_bps: float = MAX_SPREAD_BPS,
) -> SymbolManager:
    """SymbolManager 초기화"""
    global _symbol_manager
    _symbol_manager = SymbolManager(
        perp_exchange=perp_exchange,
        spot_exchange=spot_exchange,
        upbit_exchange=upbit_exchange,
        min_volume_24h=min_volume_24h,
        min_volume_24h_krw=min_volume_24h_krw,
        max_spread_bps=max_spread_bps,
    )
    return _symbol_manager
