"""4단계 프롬프트 빌더

Discovery → Generation → Optimization → Validation
프롬프트 크기 ~8-12K 토큰 목표 (기존 40K+ 대비 최적화)
"""

import json
from typing import Optional

from src.ai_optimizer.schemas import (
    BacktestSummary,
    OptimizationSession,
    PhaseType,
    StrategyCandidate,
    StrategyTypeAI,
)

# HistoryProvider API 레퍼런스 (AI에게 제공)
HISTORY_PROVIDER_API = """
## HistoryProvider API Reference
- get_candles(symbol, interval, count) -> list[Candle]  # Candle: .symbol, .open_time, .open, .high, .low, .close, .volume, .quote_volume
- get_closes(symbol, interval, count) -> list[float]
- get_highs(symbol, interval, count) -> list[float]
- get_lows(symbol, interval, count) -> list[float]
- get_volumes(symbol, interval, count) -> list[float]  # quote_volume (KRW)
- get_current_price(symbol, interval="5m") -> Optional[float]
- calc_sma(symbol, interval, period) -> Optional[float]
- calc_ema(symbol, interval, period) -> Optional[float]
- calc_rvol(symbol, interval, period=20) -> Optional[float]  # relative volume
- calc_atr(symbol, interval, period=14) -> Optional[float]  # absolute ATR
"""

# VOB 템플릿 코드 (AI 참고용)
VOB_TEMPLATE = """
# Example: VolatileOversoldBounce signal generator
class VOBSignalGenerator:
    def __init__(self, params=None):
        self.params = params or {}
        self.min_atr_pct = self.params.get("min_atr_pct", 1.0)
        self.max_rsi = self.params.get("max_rsi", 35)
        self.min_rvol = self.params.get("min_rvol", 1.5)
        self.position_pct = self.params.get("position_pct", 0.10)
        self._last_entry = {}
        self._cooldown_minutes = self.params.get("cooldown_minutes", 60)

    def __call__(self, timestamp, history, symbols):
        signals = []
        for symbol in symbols:
            signal = self._evaluate(timestamp, history, symbol)
            if signal:
                signals.append(signal)
        return signals

    def _evaluate(self, timestamp, history, symbol):
        # cooldown check
        if symbol in self._last_entry:
            elapsed = (timestamp - self._last_entry[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        candles = history.get_candles(symbol, "5m", 220)
        if len(candles) < 200:
            return None

        current = candles[-1]
        price = current.close
        if price <= 0:
            return None

        # 1. Bullish candle
        if current.close <= current.open:
            return None

        # 2. RSI < threshold
        closes = [c.close for c in candles]
        rsi = self._calc_rsi(closes, 14)
        if rsi is None or rsi >= self.max_rsi:
            return None

        # 3. ATR% >= threshold
        atr_pct = self._calc_atr_pct(candles, 14, price)
        if atr_pct is None or atr_pct < self.min_atr_pct:
            return None

        # 4. RVOL >= threshold
        rvol = history.calc_rvol(symbol, "5m", 20)
        if not rvol or rvol < self.min_rvol:
            return None

        score = 60 + min(atr_pct, 3) * 5 + (35 - rsi) * 0.5
        self._last_entry[symbol] = timestamp
        return {
            "symbol": symbol, "action": "buy",
            "strategy": "VOB", "score": score,
            "position_pct": self.position_pct,
            "indicators": {"rsi": rsi, "atr_pct": atr_pct, "rvol": rvol},
        }

    @staticmethod
    def _calc_rsi(closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(-period, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def _calc_atr_pct(candles, period, price):
        if len(candles) < period + 1:
            return None
        trs = []
        for i in range(-period, 0):
            h, lo, pc = candles[i].high, candles[i].low, candles[i - 1].close
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr = sum(trs) / len(trs)
        return (atr / price * 100) if price > 0 else None
"""

EXIT_CHECKER_TEMPLATE = """
# Example: exit checker factory
def create_exit_checker(stop_loss_pct=-0.02, take_profit_pct=0.02,
                        trailing_trigger_pct=0.015, trailing_stop_pct=0.008,
                        time_stop_minutes=180):
    def check_exit(position, current_price, history_provider):
        entry_price = position.entry_price
        pnl_pct = (current_price - entry_price) / entry_price

        # Time stop
        if time_stop_minutes > 0:
            candles = history_provider.get_candles(position.symbol, "5m", 1)
            if candles:
                current_time = candles[-1].open_time
                holding_min = (current_time - position.entry_time).total_seconds() / 60
                if holding_min >= time_stop_minutes:
                    return "time_stop"

        # Stop loss (check candle low)
        candles = history_provider.get_candles(position.symbol, "5m", 1)
        if candles:
            low_pnl = (candles[-1].low - entry_price) / entry_price
            if low_pnl <= stop_loss_pct:
                return "stop_loss"
            high_pnl = (candles[-1].high - entry_price) / entry_price
            if high_pnl >= take_profit_pct:
                return "take_profit"

        if pnl_pct <= stop_loss_pct:
            return "stop_loss"
        if pnl_pct >= take_profit_pct:
            return "take_profit"

        # Trailing stop
        if position.max_profit_pct >= trailing_trigger_pct:
            drop = position.max_profit_pct - pnl_pct
            if drop >= trailing_stop_pct:
                return "trailing_stop"
        return None
    return check_exit
"""

MARKET_CONTEXT = """
## Upbit KRW Market Characteristics
- 5-minute candles, ~115 KRW symbols
- Commission: 0.05% per side (0.1% round-trip)
- Slippage: ~5bps average
- High volatility altcoins (ATR% often > 1-3%)
- Mean-reversion works well in oversold + high-volume conditions
- Momentum/breakout works in trending markets
- BTC correlation: most altcoins follow BTC direction with lag
- RVOL (relative volume) > 1.5 is strong signal for any strategy
- RSI extremes (<30 or >70) provide edge when combined with other filters
"""

DATA_ANALYSIS_CONTEXT = """
## Historical Data Analysis Results (3mo backtest, 810K candles)
- Commission is 0.05% per side (0.1% round-trip) + 5bps slippage = total ~0.15% friction
- This means a strategy needs at least +0.2% avg win to be profitable
- ATR% > 1% is the strongest predictor of profitability
- Tight SL (-1.2%) gets hit by 5m noise → worse results
- Optimal SL range: -1.5% to -2.5% depending on volatility
- TP range: +1.5% to +3% - higher TP = fewer trades but bigger wins
- RVOL > 1.5 is the most effective noise filter (filters out 80% of bad trades)
- Best proven patterns: oversold bounce (WR 53.8%), crash recovery (WR 48.6%)
- Trailing stop tip: set trigger = TP level to avoid premature exit
- Critical: candle data has .close, .high, .low, .open, .volume, .quote_volume, .open_time attributes
- USE DEFENSIVE CODING: always check len(candles) >= required before computing indicators
- AVOID complex multi-timeframe logic that reduces trade count below 30

## What Works (proven in backtest):
1. RSI < 30-35 + bullish candle + RVOL > 1.5 → buy (mean reversion)
2. Price drops > 1.5*ATR in short period + recovery signal → buy (crash recovery)
3. Consecutive bearish candles (3+) + RSI < 25 + volume spike → buy (triple bearish reversal)

## What Does NOT Work:
1. Pure momentum (trend following) on 5m → whipsawed by noise, negative expectancy
2. Breakout without volume confirmation → too many false breakouts
3. Complex multi-indicator strategies with 10+ conditions → too few trades
4. Strategies without cooldown → rapid consecutive losses
"""

STRATEGY_TYPES_DESCRIPTION = {
    StrategyTypeAI.MOMENTUM: "Price momentum / trend-following using moving averages, ROC, ADX",
    StrategyTypeAI.BREAKOUT: "Price breakout from ranges, channels, or consolidation patterns",
    StrategyTypeAI.TREND: "Trend detection using multi-period EMAs, MACD, or Ichimoku",
    StrategyTypeAI.MEAN_REVERSION: "Oversold/overbought bounce using RSI, Bollinger, Z-score",
    StrategyTypeAI.VOLUME: "Volume-driven signals using RVOL spikes, VWAP, OBV divergence",
    StrategyTypeAI.MULTI_TIMEFRAME: "Combine 5m signals with higher timeframe (1h) trend",
    StrategyTypeAI.BTC_CORRELATION: "Trade altcoins based on BTC price action / divergence",
    StrategyTypeAI.REGIME_ADAPTIVE: "Adapt strategy based on market regime (trend/range/volatile)",
}


class PromptBuilder:
    """4단계 프롬프트 빌더"""

    SYSTEM_PROMPT = (
        "You are an expert cryptocurrency trading strategy developer for the Upbit KRW market. "
        "You create and optimize 5-minute scalping strategies based on backtested data. "
        "You write Python code that implements signal_generator and exit_checker functions "
        "compatible with the BacktestEngine. Respond ONLY in the requested JSON format."
    )

    def build_system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def build_prompt(
        self,
        session: OptimizationSession,
        round_num: int,
        backtest_summaries: Optional[dict[str, BacktestSummary]] = None,
        error_feedback: Optional[str] = None,
    ) -> str:
        """라운드에 맞는 프롬프트 생성"""
        phase = session.get_phase(round_num)

        if phase == PhaseType.DISCOVERY:
            return self._build_discovery_prompt(session, round_num, backtest_summaries)
        elif phase == PhaseType.GENERATION:
            return self._build_generation_prompt(session, round_num, backtest_summaries, error_feedback)
        elif phase == PhaseType.OPTIMIZATION:
            return self._build_optimization_prompt(session, round_num, backtest_summaries)
        else:
            return self._build_validation_prompt(session, round_num, backtest_summaries)

    def _build_discovery_prompt(
        self,
        session: OptimizationSession,
        round_num: int,
        summaries: Optional[dict[str, BacktestSummary]],
    ) -> str:
        """Discovery (라운드 1-3): 시장 특성 분석 + 전략 아이디어 생성"""
        strategy_types_text = "\n".join(
            f"- {st.value}: {desc}"
            for st, desc in STRATEGY_TYPES_DESCRIPTION.items()
        )

        existing_ideas = ""
        if session.candidates:
            existing_ideas = "\n## Existing Strategy Ideas\n"
            for c in session.get_active_candidates():
                existing_ideas += (
                    f"- [{c.strategy_type.value}] {c.name}: {c.description}\n"
                )

        round_history = self._build_round_history(session)

        return f"""# Strategy Discovery (Round {round_num}/{session.total_rounds})

{MARKET_CONTEXT}

{DATA_ANALYSIS_CONTEXT}

## Available Strategy Types
{strategy_types_text}

{HISTORY_PROVIDER_API}
{existing_ideas}
{round_history}

## Task
Propose 3 NEW strategy ideas for the Upbit 5-minute KRW market.
Each idea must be a DIFFERENT strategy type from existing ones.
Focus on strategies that can achieve > 2% return over 3 months.

CRITICAL GUIDELINES:
1. Every strategy MUST use RVOL > 1.0 or volume confirmation as a filter
2. Every strategy MUST have a cooldown (30-120 minutes between entries per symbol)
3. Prefer BUYING on weakness/dips, not on strength (commission eats momentum profits)
4. Keep indicators simple: RSI, SMA/EMA crossover, ATR, RVOL are the most reliable
5. Target: SL -1.5~-2.5%, TP +1.5~+3%, time_stop 60-240 min
6. Must generate 30+ trades over 3 months to be statistically valid

Respond in this JSON format:
```json
{{
  "market_analysis": "Brief analysis of Upbit KRW market characteristics (2-3 sentences)",
  "ideas": [
    {{
      "name": "StrategyName",
      "type": "one of: {', '.join(st.value for st in StrategyTypeAI)}",
      "description": "What the strategy does and why it has edge (2-3 sentences)",
      "entry_logic": "Specific entry conditions with indicator thresholds",
      "exit_logic": "SL/TP/trailing/time_stop parameters",
      "expected_params": {{"param_name": "default_value"}},
      "confidence": "high/medium/low"
    }}
  ]
}}
```"""

    def _build_generation_prompt(
        self,
        session: OptimizationSession,
        round_num: int,
        summaries: Optional[dict[str, BacktestSummary]],
        error_feedback: Optional[str] = None,
    ) -> str:
        """Generation (라운드 4-8): 전략 코드 생성"""
        # 구현할 후보 선택 (코드 없는 것 우선)
        unimplemented = [
            c for c in session.get_active_candidates()
            if not c.signal_code
        ]
        implemented = [
            c for c in session.get_active_candidates()
            if c.signal_code and c.total_trades > 0
        ]

        target_text = ""
        if unimplemented:
            target = unimplemented[0]
            target_text = f"""
## Target Strategy to Implement
- Name: {target.name}
- Type: {target.strategy_type.value}
- Description: {target.description}
- Parameters: {json.dumps(target.parameters, indent=2)}
"""
        elif implemented:
            # 성능 가장 나쁜 것 개선
            target = sorted(implemented, key=lambda c: c.return_pct)[0]
            target_text = f"""
## Target Strategy to Improve
- Name: {target.name} (return: {target.return_pct:+.2f}%, {target.total_trades} trades)
- Current code issues or low performance
"""

        error_text = ""
        if error_feedback:
            error_text = f"\n## Previous Attempt Error\n{error_feedback}\nPlease fix the error and try again.\n"

        backtest_text = ""
        if summaries:
            backtest_text = "\n## Current Backtest Results\n"
            for name, s in summaries.items():
                backtest_text += f"### {name}\n{s.to_prompt_text()}\n\n"

        round_history = self._build_round_history(session)

        return f"""# Strategy Code Generation (Round {round_num}/{session.total_rounds})

{HISTORY_PROVIDER_API}

## Code Template (VOB example)
```python
{VOB_TEMPLATE}
```

## Exit Checker Template
```python
{EXIT_CHECKER_TEMPLATE}
```
{target_text}
{error_text}
{backtest_text}
{round_history}

## Requirements
1. Write a complete signal_generator class with __init__(params) and __call__(timestamp, history, symbols) -> list[dict]
2. Write a complete exit_checker factory function
3. Use ONLY the HistoryProvider API methods listed above
4. Include cooldown logic to prevent repeated entries
5. Each signal dict MUST have: symbol, action ("buy"), strategy, score, position_pct, indicators
6. Keep parameter count <= 8 to avoid overfitting

Respond in this JSON format:
```json
{{
  "strategy_name": "name",
  "strategy_type": "type",
  "signal_code": "complete Python class code",
  "exit_code": "complete Python exit checker factory code",
  "parameters": {{"param": "value"}},
  "reasoning": "Why this strategy should work (2-3 sentences)"
}}
```"""

    def _build_optimization_prompt(
        self,
        session: OptimizationSession,
        round_num: int,
        summaries: Optional[dict[str, BacktestSummary]],
    ) -> str:
        """Optimization (라운드 9-12): 파라미터 최적화 + 코드 수정"""
        top_5 = session.get_top_candidates(5)

        candidates_text = ""
        for c in top_5:
            candidates_text += f"""
### {c.name} ({c.strategy_type.value})
- Return: {c.return_pct:+.2f}% | Sharpe: {c.sharpe:.2f} | MDD: {c.max_dd:.2f}%
- WR: {c.win_rate:.1f}% | PF: {c.profit_factor:.2f} | Trades: {c.total_trades}
- Params: {json.dumps(c.parameters, indent=2)}
"""
            if c.candidate_id in (summaries or {}):
                candidates_text += f"Backtest detail:\n{summaries[c.candidate_id].to_prompt_text()}\n"

        round_history = self._build_round_history(session)

        return f"""# Strategy Optimization (Round {round_num}/{session.total_rounds})

{MARKET_CONTEXT}

## Top 5 Candidates
{candidates_text}

{round_history}

## Task
For each of the top 5 candidates, suggest parameter optimizations.
Focus on:
1. SL/TP ratio adjustment based on actual win rates
2. Cooldown tuning based on consecutive loss patterns
3. Filter tightening/loosening based on trade count vs quality
4. Time stop adjustment based on holding time distribution
5. Code modifications if structural issues are found

Respond in this JSON format:
```json
{{
  "analysis": "Overall analysis across candidates (2-3 sentences)",
  "optimizations": [
    {{
      "candidate_id": "id",
      "changes": "What to change (1 sentence)",
      "new_params": {{}},
      "code_changes": "null or modified code string",
      "expected_improvement": "Why this should help (1 sentence)"
    }}
  ]
}}
```"""

    def _build_validation_prompt(
        self,
        session: OptimizationSession,
        round_num: int,
        summaries: Optional[dict[str, BacktestSummary]],
    ) -> str:
        """Validation (라운드 13-15): Walk-forward 결과 분석 + 앙상블 제안"""
        candidates_text = ""
        for c in session.get_top_candidates(10):
            wf_text = ""
            if c.wf_oos_return is not None:
                wf_text = (
                    f"  WF OOS: {c.wf_oos_return:+.2f}% | "
                    f"Sharpe: {c.wf_oos_sharpe:.2f} | "
                    f"IS/OOS: {c.wf_is_oos_ratio:.1f}% | "
                    f"{'PASS' if c.wf_passed else 'FAIL'}"
                )
            candidates_text += f"""
### {c.name} ({c.strategy_type.value})
- Return: {c.return_pct:+.2f}% | Sharpe: {c.sharpe:.2f} | MDD: {c.max_dd:.2f}%
- WR: {c.win_rate:.1f}% | PF: {c.profit_factor:.2f} | Trades: {c.total_trades}
- {wf_text}
"""

        return f"""# Strategy Validation & Ensemble (Round {round_num}/{session.total_rounds})

## Candidates with Walk-Forward Results
{candidates_text}

## Task
1. Analyze walk-forward results: which strategies are robust vs overfit?
2. Suggest the best ensemble (2-4 strategies with low correlation)
3. For each ensemble member, suggest final parameter adjustments
4. Consider strategy type diversity (don't pick 3 mean-reversion strategies)

Respond in this JSON format:
```json
{{
  "validation_analysis": "Analysis of overfitting risk (2-3 sentences)",
  "ensemble": [
    {{
      "candidate_id": "id",
      "role": "What role this strategy plays in the ensemble",
      "final_params": {{}},
      "allocation_pct": 25
    }}
  ],
  "rejected_reasons": {{
    "candidate_id": "Why this strategy was not selected"
  }},
  "expected_ensemble_return": "+X.XX%"
}}
```"""

    def _build_round_history(self, session: OptimizationSession) -> str:
        """이전 라운드 요약"""
        if not session.rounds:
            return ""

        lines = ["\n## Previous Rounds"]
        for r in session.rounds[-5:]:  # 최근 5 라운드만
            lines.append(
                f"- R{r.round_num} [{r.phase.value}]: "
                f"best {r.best_return_pct:+.2f}% | "
                f"created {len(r.candidates_created)} | "
                f"modified {len(r.candidates_modified)}"
            )
            if r.errors:
                lines.append(f"  Errors: {', '.join(r.errors[:2])}")

        return "\n".join(lines)
