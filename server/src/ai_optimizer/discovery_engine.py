"""AI Strategy Discovery Engine

전체 파이프라인 오케스트레이션:
1. 데이터 확인
2. 15라운드 최적화 실행
3. Walk-forward 검증
4. 앙상블 분석
5. 결과 저장 + 전략 코드 출력
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import structlog
from openai import AsyncOpenAI

from src.ai_optimizer.ensemble import EnsembleAnalyzer
from src.ai_optimizer.round_controller import RoundController
from src.ai_optimizer.schemas import OptimizationSession, PhaseType, StrategyCandidate
from src.ai_optimizer.strategy_selector import SelectionCriteria, StrategySelector

logger = structlog.get_logger()

# 기본 70개 심볼
DEFAULT_SYMBOLS = [
    "KRW-BTC", "KRW-ETH", "KRW-DOGE", "KRW-SHIB", "KRW-TRX",
    "KRW-ETC", "KRW-PEPE", "KRW-NEAR", "KRW-BCH", "KRW-HBAR",
    "KRW-SAND", "KRW-SEI", "KRW-ARB", "KRW-FIL", "KRW-UNI",
    "KRW-APT", "KRW-ATOM", "KRW-AAVE", "KRW-AVNT", "KRW-VIRTUAL",
    "KRW-USDT", "KRW-MOVE", "KRW-0G", "KRW-PENGU", "KRW-FLOW",
    "KRW-POL", "KRW-AXS", "KRW-LAYER", "KRW-WLD", "KRW-KAITO",
    "KRW-CTC", "KRW-ME", "KRW-BOUNTY", "KRW-BORA", "KRW-WET",
    "KRW-CRO", "KRW-TRUMP", "KRW-NOM", "KRW-ANIME", "KRW-BONK",
    "KRW-WAVES", "KRW-SONIC", "KRW-DOOD", "KRW-BLUR", "KRW-AXL",
    "KRW-BLAST", "KRW-NEO", "KRW-AERGO", "KRW-ALGO", "KRW-MOCA",
    "KRW-TRUST", "KRW-CELO", "KRW-AKT", "KRW-EGLD", "KRW-LSK",
    "KRW-WCT", "KRW-W", "KRW-ERA", "KRW-IN", "KRW-CARV",
    "KRW-AGLD", "KRW-PLUME", "KRW-ID", "KRW-G", "KRW-NXPC",
    "KRW-JTO", "KRW-DKA", "KRW-IOTA", "KRW-IP", "KRW-A",
]


class DiscoveryEngine:
    """AI 전략 발견 엔진"""

    def __init__(
        self,
        openai_api_key: str,
        symbols: Optional[list[str]] = None,
        total_rounds: int = 15,
        initial_capital: float = 1_000_000,
    ):
        self._openai_client = AsyncOpenAI(api_key=openai_api_key)
        self._symbols = symbols or DEFAULT_SYMBOLS
        self._total_rounds = total_rounds
        self._initial_capital = initial_capital

        self._session: Optional[OptimizationSession] = None
        self._report_dir = Path("reports/analysis")
        self._generated_dir = Path("src/strategies/generated")

    async def run(self) -> dict:
        """전체 파이프라인 실행"""
        session_id = f"opt_{uuid.uuid4().hex[:8]}"

        self._session = OptimizationSession(
            session_id=session_id,
            total_rounds=self._total_rounds,
            symbols=self._symbols,
            initial_capital=self._initial_capital,
        )

        self._print_header()

        # 라운드 컨트롤러 생성
        controller = RoundController(
            self._openai_client,
            self._session,
        )

        # === 15 라운드 실행 ===
        for round_num in range(1, self._total_rounds + 1):
            phase = self._session.get_phase(round_num)
            self._print_round_header(round_num, phase)

            result = await controller.run_round(round_num)

            self._print_round_result(round_num, result)

            # 중간 저장 (5라운드마다)
            if round_num % 5 == 0:
                self._save_intermediate_results()

        self._session.end_time = datetime.utcnow()

        # === 최종 선별 ===
        selected, rejected = self._select_final_strategies()

        # === 앙상블 분석 ===
        ensemble_result = self._analyze_ensemble(selected)

        # === 결과 저장 ===
        final_report = self._build_final_report(selected, rejected, ensemble_result)
        self._save_results(final_report)

        # === 전략 코드 출력 ===
        self._save_generated_strategies(selected)

        self._print_final_summary(final_report)

        return final_report

    def _select_final_strategies(self):
        """최종 전략 선별"""
        selector = StrategySelector(SelectionCriteria(
            min_return_pct=2.0,
            min_sharpe=0.3,
            max_drawdown_pct=20.0,
            min_win_rate=35.0,
            min_profit_factor=1.0,
            min_trades=30,
            require_wf_pass=False,
        ))

        candidates = self._session.get_active_candidates()
        return selector.select(candidates, max_results=10)

    def _analyze_ensemble(self, selected):
        """앙상블 분석"""
        analyzer = EnsembleAnalyzer()
        candidates = [sc.candidate for sc in selected]
        return analyzer.analyze(candidates)

    def _build_final_report(self, selected, rejected, ensemble_result) -> dict:
        """최종 보고서 빌드"""
        report = {
            "session_id": self._session.session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "config": {
                "total_rounds": self._total_rounds,
                "symbols_count": len(self._symbols),
                "initial_capital": self._initial_capital,
            },
            "statistics": {
                "total_backtests": self._session.total_backtests_run,
                "total_openai_calls": self._session.total_openai_calls,
                "total_cost_usd": round(self._session.total_cost_usd, 2),
                "total_candidates_created": len(self._session.candidates),
                "candidates_selected": len(selected),
                "candidates_rejected": len(rejected),
                "duration_minutes": round(
                    (self._session.end_time - self._session.start_time).total_seconds() / 60, 1
                ) if self._session.end_time else 0,
            },
            "selected_strategies": [
                {
                    "rank": sc.rank,
                    "score": round(sc.score, 2),
                    "name": sc.candidate.name,
                    "type": sc.candidate.strategy_type.value,
                    "return_pct": sc.candidate.return_pct,
                    "sharpe": sc.candidate.sharpe,
                    "max_dd": sc.candidate.max_dd,
                    "win_rate": sc.candidate.win_rate,
                    "profit_factor": sc.candidate.profit_factor,
                    "total_trades": sc.candidate.total_trades,
                    "wf_passed": sc.candidate.wf_passed,
                    "wf_oos_return": sc.candidate.wf_oos_return,
                    "parameters": sc.candidate.parameters,
                }
                for sc in selected
            ],
            "rejected_strategies": [
                {
                    "name": sc.candidate.name,
                    "type": sc.candidate.strategy_type.value,
                    "return_pct": sc.candidate.return_pct,
                    "reason": sc.rejection_reason,
                }
                for sc in rejected[:10]
            ],
            "ensemble": {
                "members": [
                    {
                        "name": m.candidate.name,
                        "type": m.candidate.strategy_type.value,
                        "allocation_pct": m.allocation_pct,
                        "role": m.role,
                    }
                    for m in ensemble_result.members
                ],
                "type_diversity": ensemble_result.type_diversity,
                "max_correlation": round(ensemble_result.max_correlation, 3),
                "expected_return_pct": round(ensemble_result.expected_return_pct, 2),
                "expected_sharpe": round(ensemble_result.expected_sharpe, 2),
            },
            "rounds": [
                {
                    "round": r.round_num,
                    "phase": r.phase.value,
                    "best_return_pct": r.best_return_pct,
                    "candidates_created": len(r.candidates_created),
                    "candidates_modified": len(r.candidates_modified),
                    "errors": r.errors,
                }
                for r in self._session.rounds
            ],
        }

        return report

    def _save_results(self, report: dict) -> None:
        """결과 JSON 저장"""
        self._report_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._report_dir / "ai_optimization_results.json"

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        logger.info("Results saved", path=str(output_path))

    def _save_generated_strategies(self, selected) -> None:
        """선별된 전략 코드를 파일로 저장"""
        self._generated_dir.mkdir(parents=True, exist_ok=True)

        # __init__.py
        init_path = self._generated_dir / "__init__.py"
        with open(init_path, "w") as f:
            f.write('"""AI-generated strategies"""\n')

        for sc in selected:
            candidate = sc.candidate
            if not candidate.signal_code:
                continue

            filename = f"{candidate.name.lower().replace(' ', '_')}.py"
            filepath = self._generated_dir / filename

            code = f'"""{candidate.name}\n\n'
            code += f"Type: {candidate.strategy_type.value}\n"
            code += f"Description: {candidate.description}\n"
            code += f"Return: {candidate.return_pct:+.2f}% | Sharpe: {candidate.sharpe:.2f}\n"
            code += f"WF Passed: {candidate.wf_passed}\n"
            code += f'Generated: {datetime.utcnow().isoformat()}\n"""\n\n'

            code += f"PARAMETERS = {json.dumps(candidate.parameters, indent=4)}\n\n"
            code += "# Signal Generator\n"
            code += candidate.signal_code + "\n\n"

            if candidate.exit_code:
                code += "# Exit Checker\n"
                code += candidate.exit_code + "\n"

            with open(filepath, "w") as f:
                f.write(code)

            logger.info("Strategy code saved", path=str(filepath))

    def _save_intermediate_results(self) -> None:
        """중간 결과 저장"""
        self._report_dir.mkdir(parents=True, exist_ok=True)
        path = self._report_dir / f"ai_opt_intermediate_{self._session.current_round}.json"

        data = {
            "session_id": self._session.session_id,
            "current_round": self._session.current_round,
            "candidates": {
                cid: {
                    "name": c.name,
                    "type": c.strategy_type.value,
                    "return_pct": c.return_pct,
                    "sharpe": c.sharpe,
                    "trades": c.total_trades,
                    "active": c.is_active,
                }
                for cid, c in self._session.candidates.items()
            },
            "total_backtests": self._session.total_backtests_run,
            "total_cost_usd": round(self._session.total_cost_usd, 2),
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def _print_header(self) -> None:
        """헤더 출력"""
        print(f"\n{'=' * 100}")
        print(f"  AI Strategy Discovery Engine v1.0")
        print(f"{'=' * 100}")
        print(f"  Session: {self._session.session_id}")
        print(f"  Rounds: {self._total_rounds} | Symbols: {len(self._symbols)} | Capital: {self._initial_capital:,.0f} KRW")
        print(f"  Phases: Discovery(1-3) → Generation(4-8) → Optimization(9-12) → Validation(13-15)")
        print(f"  Strategy Types: 8 (momentum, breakout, trend, mean_reversion, volume, multi_tf, btc_corr, regime)")
        print(f"{'=' * 100}")

    def _print_round_header(self, round_num: int, phase: PhaseType) -> None:
        """라운드 헤더"""
        phase_emoji = {
            PhaseType.DISCOVERY: "DISCOVER",
            PhaseType.GENERATION: "GENERATE",
            PhaseType.OPTIMIZATION: "OPTIMIZE",
            PhaseType.VALIDATION: "VALIDATE",
        }
        print(f"\n  {'─' * 90}")
        print(f"  Round {round_num}/{self._total_rounds} [{phase_emoji.get(phase, phase.value)}]")
        print(f"  {'─' * 90}")

    def _print_round_result(self, round_num: int, result) -> None:
        """라운드 결과"""
        status = "OK" if not result.errors else "WARN"
        print(f"  [{status}] Created: {len(result.candidates_created)} | "
              f"Modified: {len(result.candidates_modified)} | "
              f"Best: {result.best_return_pct:+.2f}%")

        if result.errors:
            for err in result.errors[:3]:
                print(f"       Error: {err[:100]}")

        # 현재 상위 3개 후보
        top_3 = self._session.get_top_candidates(3)
        if top_3:
            print(f"  Top candidates:")
            for i, c in enumerate(top_3, 1):
                print(f"    {i}. {c.name} ({c.strategy_type.value}): "
                      f"{c.return_pct:+.2f}% | Sharpe {c.sharpe:.2f} | "
                      f"{c.total_trades} trades")

    def _print_final_summary(self, report: dict) -> None:
        """최종 요약"""
        stats = report["statistics"]
        print(f"\n{'=' * 100}")
        print(f"  FINAL RESULTS")
        print(f"{'=' * 100}")
        print(f"  Duration: {stats['duration_minutes']} min | "
              f"Backtests: {stats['total_backtests']} | "
              f"OpenAI calls: {stats['total_openai_calls']} | "
              f"Cost: ${stats['total_cost_usd']:.2f}")
        print(f"  Candidates: {stats['total_candidates_created']} created → "
              f"{stats['candidates_selected']} selected / {stats['candidates_rejected']} rejected")

        print(f"\n  Selected Strategies:")
        for s in report["selected_strategies"]:
            wf_text = "WF:PASS" if s["wf_passed"] else "WF:FAIL"
            print(f"    #{s['rank']} {s['name']} ({s['type']}): "
                  f"{s['return_pct']:+.2f}% | Sharpe {s['sharpe']:.2f} | "
                  f"MDD {s['max_dd']:.2f}% | WR {s['win_rate']:.1f}% | "
                  f"{wf_text}")

        ensemble = report["ensemble"]
        if ensemble["members"]:
            print(f"\n  Ensemble ({ensemble['type_diversity']} types, "
                  f"max corr {ensemble['max_correlation']:.2f}):")
            for m in ensemble["members"]:
                print(f"    - {m['name']} ({m['type']}): {m['allocation_pct']:.1f}% - {m['role']}")
            print(f"    Expected: {ensemble['expected_return_pct']:+.2f}% return, "
                  f"Sharpe {ensemble['expected_sharpe']:.2f}")

        print(f"\n  Results: reports/analysis/ai_optimization_results.json")
        print(f"  Strategies: src/strategies/generated/")
        print(f"\n{'=' * 100}\n")
