#!/usr/bin/env python3
"""
매매 내역 분석 스크립트 (2026-02-06 목요일 ~ 현재)

목적: 목요일부터 현재까지의 모든 거래를 분석하고
전략별 손익과 손실 원인을 파악하여 한국어 보고서 생성
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple
import json

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import select, func
from src.models.database import async_session, OrderModel, TradeModel, PositionLedgerModel, EventModel
from src.models.schemas import StrategyType, OrderSide, OrderStatus

# 전략 한국어 이름
STRATEGY_NAMES = {
    "ATTACK": "급등주 공격",
    "REBOUND": "반등 스캘핑",
    "PULLBACK": "눌림목 매수",
    "SATELLITE": "5분봉 스캐너",
    "DIP_SCALPER": "급락 스캘핑",
    "IGNITION": "점화 전략",
    "SURGE": "급등 감지",
    "CORE": "방어적 코어",
}

# 손실 원인 한국어 이름
LOSS_REASON_NAMES = {
    "stop_loss": "하드 스톱 로스",
    "time_stop": "타임 스톱",
    "trailing_stop": "트레일링 스톱",
    "volatile_exit": "변동성 청산",
    "weak_exit": "약세 청산",
    "extreme_exit": "극한 청산",
    "manual": "수동 청산",
    "unknown": "기타/불명",
}


class TradeAnalyzer:
    """거래 분석기"""

    def __init__(self, start_date: datetime):
        self.start_date = start_date
        self.start_date_utc = start_date - timedelta(hours=9)  # KST → UTC
        self.orders: List[OrderModel] = []
        self.trades: List[TradeModel] = []
        self.matched_trades: List = []  # 매수-매도 매칭된 거래
        self.strategy_stats: Dict = defaultdict(
            lambda: {
                "trades": [],
                "buy_count": 0,
                "sell_count": 0,
                "total_pnl": 0.0,
                "total_fees": 0.0,
                "win_count": 0,
                "loss_count": 0,
                "max_profit": 0.0,
                "max_loss": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            }
        )
        self.loss_by_reason: Dict = defaultdict(
            lambda: {"count": 0, "total_loss": 0.0, "trades": []}
        )

    async def load_trades(self):
        """데이터베이스에서 주문 내역 로드 (OrderModel)"""
        print(f"📊 주문 내역 로딩 중... (시작: {self.start_date.strftime('%Y-%m-%d %H:%M KST')})")

        async with async_session() as session:
            # 체결 완료된 주문만 가져오기
            result = await session.execute(
                select(OrderModel)
                .where(
                    OrderModel.created_at >= self.start_date_utc,
                    OrderModel.status.in_([OrderStatus.FILLED, OrderStatus.SYNCED])
                )
                .order_by(OrderModel.created_at)
            )
            self.orders = result.scalars().all()

        print(f"✓ {len(self.orders)}건의 주문 로드 완료")

        # 매수/매도 카운트
        buy_count = sum(1 for o in self.orders if o.side == OrderSide.BUY)
        sell_count = sum(1 for o in self.orders if o.side == OrderSide.SELL)
        print(f"  매수: {buy_count}건, 매도: {sell_count}건\n")

        # 전략별 주문 분포
        strategy_counts = defaultdict(int)
        for order in self.orders:
            strategy_counts[order.strategy.value] += 1

        print("전략별 주문 수:")
        for strategy, count in sorted(strategy_counts.items(), key=lambda x: -x[1]):
            print(f"  {STRATEGY_NAMES.get(strategy, strategy)}: {count}건")
        print()

    def match_buy_sell_orders(self):
        """매수-매도 주문을 매칭하여 거래 생성"""
        print("🔗 매수-매도 매칭 중...")

        # 심볼별, 전략별로 그룹화
        groups = defaultdict(lambda: {"buys": [], "sells": []})

        for order in self.orders:
            key = (order.symbol, order.strategy.value)
            if order.side == OrderSide.BUY:
                groups[key]["buys"].append(order)
            else:
                groups[key]["sells"].append(order)

        # 각 그룹에서 매수-매도 매칭
        for (symbol, strategy), orders in groups.items():
            buys = sorted(orders["buys"], key=lambda o: o.created_at)
            sells = sorted(orders["sells"], key=lambda o: o.created_at)

            # FIFO 방식으로 매칭
            buy_idx = 0
            for sell in sells:
                if buy_idx >= len(buys):
                    # 매수 없이 매도만 있는 경우 (이전 세션 포지션)
                    break

                buy = buys[buy_idx]
                remaining_sell_qty = sell.filled_quantity

                while remaining_sell_qty > 0 and buy_idx < len(buys):
                    buy = buys[buy_idx]
                    matched_qty = min(remaining_sell_qty, buy.filled_quantity)

                    # avg_fill_price가 None인 경우 price 사용
                    buy_price = buy.avg_fill_price if buy.avg_fill_price else buy.price
                    sell_price = sell.avg_fill_price if sell.avg_fill_price else sell.price

                    if not buy_price or not sell_price:
                        # 가격 정보 없으면 스킵
                        buy_idx += 1
                        continue

                    # PnL 계산
                    buy_value = matched_qty * buy_price
                    sell_value = matched_qty * sell_price
                    pnl = sell_value - buy_value

                    # KST 시간 변환
                    sell_time_kst = sell.created_at + timedelta(hours=9)

                    # 거래 기록 생성
                    trade = {
                        "symbol": symbol,
                        "strategy": strategy,
                        "buy_order": buy,
                        "sell_order": sell,
                        "quantity": matched_qty,
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "pnl": pnl,
                        "pnl_pct": (pnl / buy_value) * 100 if buy_value > 0 else 0,
                        "sell_time": sell_time_kst,
                        "hold_time": (sell.created_at - buy.created_at).total_seconds() / 60,  # 분
                    }

                    self.matched_trades.append(trade)

                    # 남은 수량 업데이트
                    buy.filled_quantity -= matched_qty
                    remaining_sell_qty -= matched_qty

                    if buy.filled_quantity <= 0.0001:  # 부동소수점 오차 고려
                        buy_idx += 1

        print(f"✓ {len(self.matched_trades)}건의 매칭된 거래 생성 완료\n")

    async def load_loss_reasons(self):
        """손실 원인 분석 (매칭된 거래 기준)"""
        print("🔍 손실 원인 분석 중...")

        async with async_session() as session:
            # 손실 거래만 필터링
            loss_trades = [t for t in self.matched_trades if t["pnl"] < 0]

            for trade in loss_trades:
                # 손실 원인 추정
                reason = await self._infer_loss_reason_from_matched(trade, session)

                self.loss_by_reason[reason]["count"] += 1
                self.loss_by_reason[reason]["total_loss"] += trade["pnl"]
                self.loss_by_reason[reason]["trades"].append(trade)

        print(f"✓ 손실 거래 {sum(r['count'] for r in self.loss_by_reason.values())}건 분석 완료\n")

    async def _infer_loss_reason_from_matched(self, trade: dict, session) -> str:
        """손실 원인 추정 (매칭된 거래)"""
        symbol = trade["symbol"]
        sell_time = trade["sell_order"].created_at
        pnl_pct = trade["pnl_pct"]
        hold_time = trade["hold_time"]

        # 1. 이벤트 로그 확인
        event_result = await session.execute(
            select(EventModel)
            .where(
                EventModel.timestamp.between(
                    sell_time - timedelta(minutes=1), sell_time + timedelta(minutes=1)
                ),
                EventModel.message.contains(symbol),
            )
            .limit(10)
        )
        events = event_result.scalars().all()

        for event in events:
            msg = event.message.lower()
            if "stop loss" in msg or "sl hit" in msg or "손절" in msg:
                return "stop_loss"
            elif "time stop" in msg or "time limit" in msg:
                return "time_stop"
            elif "trailing" in msg:
                return "trailing_stop"
            elif "volatile" in msg or "변동성" in msg:
                return "volatile_exit"
            elif "weak" in msg:
                return "weak_exit"
            elif "extreme" in msg:
                return "extreme_exit"
            elif "manual" in msg or "수동" in msg:
                return "manual"

        # 2. 휴리스틱 분석 (손익률 기반 추정)
        if pnl_pct < -1.0:
            return "stop_loss"
        elif -0.5 < pnl_pct < -0.1:
            return "trailing_stop"
        elif -0.1 < pnl_pct < 0:
            if hold_time > 60:
                return "time_stop"
            else:
                return "trailing_stop"

        return "unknown"

    async def _infer_loss_reason(self, trade: TradeModel, session) -> str:
        """손실 원인 추정"""
        symbol = trade.symbol
        executed_at = trade.executed_at

        # 1. 포지션 메타데이터 확인
        result = await session.execute(
            select(PositionLedgerModel)
            .where(
                PositionLedgerModel.symbol == symbol,
                PositionLedgerModel.last_fill_time.between(
                    executed_at - timedelta(minutes=5), executed_at + timedelta(minutes=5)
                ),
            )
            .limit(1)
        )
        position = result.scalars().first()

        if position and position.metadata_json:
            try:
                metadata = json.loads(position.metadata_json)
                exit_reason = metadata.get("exit_reason")
                if exit_reason:
                    return exit_reason
            except:
                pass

        # 2. 이벤트 로그 확인
        event_result = await session.execute(
            select(EventModel)
            .where(
                EventModel.timestamp.between(
                    executed_at - timedelta(minutes=1), executed_at + timedelta(minutes=1)
                ),
                EventModel.message.contains(symbol),
            )
            .limit(10)
        )
        events = event_result.scalars().all()

        for event in events:
            msg = event.message.lower()
            if "stop loss" in msg or "sl hit" in msg or "손절" in msg:
                return "stop_loss"
            elif "time stop" in msg or "time limit" in msg:
                return "time_stop"
            elif "trailing" in msg:
                return "trailing_stop"
            elif "volatile" in msg or "변동성" in msg:
                return "volatile_exit"
            elif "weak" in msg:
                return "weak_exit"
            elif "extreme" in msg:
                return "extreme_exit"
            elif "manual" in msg or "수동" in msg:
                return "manual"

        # 3. 휴리스틱 분석 (손익률 기반 추정)
        pnl_pct = trade.realized_pnl / (trade.price * trade.quantity) if trade.price > 0 else 0

        if -0.02 < pnl_pct < -0.008:
            return "stop_loss"
        elif -0.008 < pnl_pct < -0.002:
            return "trailing_stop"
        elif -0.002 < pnl_pct < 0:
            return "time_stop"

        return "unknown"

    def analyze_by_strategy(self):
        """전략별 통계 집계 (매칭된 거래 기준)"""
        print("📈 전략별 통계 집계 중...")

        for trade in self.matched_trades:
            strategy = trade["strategy"]
            stats = self.strategy_stats[strategy]

            # 거래 추가
            stats["trades"].append(trade)

            # 매수/매도 카운트
            stats["buy_count"] += 1  # 매칭된 거래는 항상 매수+매도 쌍
            stats["sell_count"] += 1

            # 손익 집계
            pnl = trade["pnl"]
            fee = 0  # 수수료는 별도 계산 필요

            stats["total_pnl"] += pnl
            stats["total_fees"] += fee

            if pnl > 0:
                stats["win_count"] += 1
                stats["gross_profit"] += pnl
                stats["max_profit"] = max(stats["max_profit"], pnl)
            else:
                stats["loss_count"] += 1
                stats["gross_loss"] += abs(pnl)
                stats["max_loss"] = min(stats["max_loss"], pnl)

        print(f"✓ {len(self.strategy_stats)}개 전략 분석 완료\n")

    def generate_report(self) -> str:
        """한국어 보고서 생성"""
        lines = []
        lines.append("=" * 80)
        lines.append(f"📊 거래 성과 분석 ({self.start_date.strftime('%Y-%m-%d')} ~ 현재)")
        lines.append("=" * 80)
        lines.append("")

        # 1. 전체 요약
        lines.append(self._generate_summary())
        lines.append("")

        # 2. 전략별 성과
        lines.append(self._generate_strategy_breakdown())
        lines.append("")

        # 3. 손실 거래 분석
        lines.append(self._generate_loss_analysis())
        lines.append("")

        # 4. 상세 거래 내역 (최근 30건)
        lines.append(self._generate_detailed_trades(limit=30))

        return "\n".join(lines)

    def _generate_summary(self) -> str:
        """전체 요약 생성 (매칭된 거래 기준)"""
        total_trades = len(self.matched_trades)
        total_pnl = sum(t["pnl"] for t in self.matched_trades)
        total_fees = 0  # 수수료는 별도 계산 필요
        net_pnl = total_pnl - total_fees

        win_count = len([t for t in self.matched_trades if t["pnl"] > 0])
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

        lines = []
        lines.append("📌 전체 요약")
        lines.append("-" * 80)
        lines.append(f"총 거래 수: {total_trades:,}건")
        lines.append(f"총 손익: {total_pnl:,.0f}원")
        lines.append(f"총 수수료: {total_fees:,.0f}원")
        lines.append(f"순손익: {net_pnl:,.0f}원")
        lines.append(f"승률: {win_rate:.1f}% ({win_count}승 {total_trades - win_count}패)")
        lines.append(f"평균 손익: {avg_pnl:,.0f}원")

        return "\n".join(lines)

    def _generate_strategy_breakdown(self) -> str:
        """전략별 성과 생성"""
        lines = []
        lines.append("🎯 전략별 성과")
        lines.append("-" * 80)

        # 손익 기준 정렬
        sorted_strategies = sorted(
            self.strategy_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True
        )

        for idx, (strategy, stats) in enumerate(sorted_strategies, 1):
            strategy_name = STRATEGY_NAMES.get(strategy, strategy)
            total_trades = stats["sell_count"]

            if total_trades == 0:
                continue

            net_pnl = stats["total_pnl"] - stats["total_fees"]
            win_rate = (
                stats["win_count"] / total_trades * 100 if total_trades > 0 else 0
            )
            avg_pnl = stats["total_pnl"] / total_trades if total_trades > 0 else 0
            profit_factor = (
                stats["gross_profit"] / stats["gross_loss"]
                if stats["gross_loss"] > 0
                else 0
            )

            lines.append(f"\n{idx}. {strategy_name} ({strategy})")
            lines.append(f"   거래 수: {total_trades:,}건 (매수 {stats['buy_count']} / 매도 {stats['sell_count']})")
            lines.append(f"   총 손익: {stats['total_pnl']:,.0f}원")
            lines.append(f"   순손익: {net_pnl:,.0f}원 (수수료 {stats['total_fees']:,.0f}원)")
            lines.append(
                f"   승률: {win_rate:.1f}% ({stats['win_count']}승 {stats['loss_count']}패)"
            )
            lines.append(f"   평균 손익: {avg_pnl:,.0f}원")
            lines.append(f"   최대 수익: {stats['max_profit']:,.0f}원")
            lines.append(f"   최대 손실: {stats['max_loss']:,.0f}원")
            if profit_factor > 0:
                lines.append(f"   수익 팩터: {profit_factor:.2f}")

        return "\n".join(lines)

    def _generate_loss_analysis(self) -> str:
        """손실 거래 분석 생성"""
        lines = []
        total_loss_trades = sum(r["count"] for r in self.loss_by_reason.values())
        total_loss_amount = sum(r["total_loss"] for r in self.loss_by_reason.values())

        lines.append(f"💔 손실 거래 분석 (총 {total_loss_trades}건, {total_loss_amount:,.0f}원)")
        lines.append("-" * 80)

        if total_loss_trades == 0:
            lines.append("✓ 손실 거래 없음!")
            return "\n".join(lines)

        # 손실 금액 기준 정렬
        sorted_reasons = sorted(
            self.loss_by_reason.items(), key=lambda x: x[1]["total_loss"]
        )

        for idx, (reason, data) in enumerate(sorted_reasons, 1):
            reason_name = LOSS_REASON_NAMES.get(reason, reason)
            count = data["count"]
            total_loss = data["total_loss"]
            avg_loss = total_loss / count if count > 0 else 0

            lines.append(f"\n{idx}. {reason_name}")
            lines.append(f"   거래 수: {count}건")
            lines.append(f"   총 손실: {total_loss:,.0f}원")
            lines.append(f"   평균 손실: {avg_loss:,.0f}원")

            # 주요 종목 (상위 3개)
            symbol_losses = defaultdict(list)
            for trade in data["trades"]:
                symbol_losses[trade["symbol"]].append(trade["pnl"])

            top_symbols = sorted(
                symbol_losses.items(), key=lambda x: (len(x[1]), sum(x[1])), reverse=True
            )[:3]

            if top_symbols:
                symbol_strs = [f"{sym} ({len(losses)}건)" for sym, losses in top_symbols]
                lines.append(f"   주요 종목: {', '.join(symbol_strs)}")

        return "\n".join(lines)

    def _generate_detailed_trades(self, limit: int = 30) -> str:
        """상세 거래 내역 생성 (매칭된 거래 기준)"""
        lines = []
        lines.append(f"📝 상세 거래 내역 (최근 {limit}건)")
        lines.append("-" * 80)

        # 최근 거래부터 표시
        recent_trades = list(reversed(self.matched_trades))[:limit]

        for trade in recent_trades:
            kst_time = trade["sell_time"]
            strategy = STRATEGY_NAMES.get(trade["strategy"], trade["strategy"])
            symbol = trade["symbol"]
            quantity = trade["quantity"]
            buy_price = trade["buy_price"]
            sell_price = trade["sell_price"]
            pnl = trade["pnl"]
            pnl_pct = trade["pnl_pct"]
            hold_time = trade["hold_time"]

            fee = 0  # 수수료는 별도 계산 필요
            net_pnl = pnl - fee

            pnl_sign = "+" if pnl >= 0 else ""
            net_sign = "+" if net_pnl >= 0 else ""

            lines.append(
                f"\n[{kst_time.strftime('%Y-%m-%d %H:%M:%S')} KST] {strategy} - {symbol}"
            )
            lines.append(
                f"  매수: {quantity:.8f} @ {buy_price:,.0f}원 (총 {buy_price * quantity:,.0f}원)"
            )
            lines.append(
                f"  매도: {quantity:.8f} @ {sell_price:,.0f}원 (총 {sell_price * quantity:,.0f}원)"
            )
            lines.append(f"  손익: {pnl_sign}{pnl:,.0f}원 ({pnl_sign}{pnl_pct:.2f}%)")
            lines.append(f"  수수료: -{fee:,.0f}원")
            lines.append(f"  순손익: {net_sign}{net_pnl:,.0f}원")
            lines.append(f"  보유 시간: {hold_time:.0f}분")

        return "\n".join(lines)


async def main():
    """메인 함수"""
    print("\n🚀 매매 내역 분석 시작\n")

    # 2026-02-06 00:00:00 KST
    start_date = datetime(2026, 2, 6, 0, 0, 0)

    analyzer = TradeAnalyzer(start_date)

    # 1. 주문 내역 로드 (OrderModel)
    await analyzer.load_trades()

    # 2. 매수-매도 매칭
    analyzer.match_buy_sell_orders()

    # 3. 전략별 통계 집계
    analyzer.analyze_by_strategy()

    # 4. 손실 원인 분석 (매칭된 거래 기준)
    await analyzer.load_loss_reasons()

    # 4. 보고서 생성
    report = analyzer.generate_report()

    # 5. 출력
    print(report)

    # 6. 파일 저장
    report_dir = project_root / "reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / "trading_analysis_feb6_2026.md"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n✅ 보고서 저장 완료: {report_file}")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
