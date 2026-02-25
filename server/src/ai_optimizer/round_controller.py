"""라운드 컨트롤러 (핵심)

| 라운드 | 단계 | 목표 |
|--------|------|------|
| 1-3  | Discovery    | 8가지 전략 유형에서 9개 아이디어 생성 |
| 4-8  | Generation   | 아이디어 → 코드 구현 + 빠른 백테스트(30일, 20심볼) |
| 9-12 | Optimization | 상위 5개 후보 전체 백테스트(3개월, 70심볼) + 파라미터 최적화 |
| 13-15| Validation   | Walk-forward 검증 + 앙상블 분석 + 최종 선별 |
"""

import json
import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Optional

import structlog
from openai import AsyncOpenAI

from src.ai_optimizer.prompt_builder import PromptBuilder
from src.ai_optimizer.schemas import (
    BacktestSummary,
    OptimizationSession,
    PhaseType,
    RoundResult,
    StrategyCandidate,
    StrategyTypeAI,
)
from src.ai_optimizer.strategy_codegen import StrategyCodegen
from src.ai_optimizer.walk_forward import WalkForwardValidator
from src.backtesting.data.data_loader import BacktestDataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models.schemas import BacktestConfig
from src.models.database import async_session

logger = structlog.get_logger()

OPENAI_MODEL = "gpt-4o"
MAX_CODE_RETRIES = 2

# 빠른 백테스트용 심볼 (상위 20개)
QUICK_SYMBOLS = [
    "KRW-BTC", "KRW-ETH", "KRW-DOGE", "KRW-SHIB", "KRW-TRX",
    "KRW-ETC", "KRW-PEPE", "KRW-NEAR", "KRW-BCH", "KRW-HBAR",
    "KRW-SAND", "KRW-SEI", "KRW-ARB", "KRW-FIL", "KRW-UNI",
    "KRW-APT", "KRW-ATOM", "KRW-AAVE", "KRW-AVNT", "KRW-VIRTUAL",
]


class RoundController:
    """15라운드 최적화 컨트롤러"""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        session: OptimizationSession,
    ):
        self._client = openai_client
        self._session = session
        self._prompt_builder = PromptBuilder()
        self._codegen = StrategyCodegen()

    def _fresh_compile(self, candidate: StrategyCandidate) -> tuple[Optional[callable], Optional[callable], list[str]]:
        """전략을 새로 컴파일 (내부 상태 초기화).

        Signal generator 클래스의 _last_entry 등 내부 상태가 백테스트 간
        누적되지 않도록 매번 새 인스턴스를 생성합니다.
        """
        return self._codegen.compile_strategy(candidate)

    async def run_round(self, round_num: int) -> RoundResult:
        """단일 라운드 실행"""
        phase = self._session.get_phase(round_num)
        self._session.current_round = round_num
        self._session.current_phase = phase

        logger.info("Starting round", round=round_num, phase=phase.value)

        result = RoundResult(round_num=round_num, phase=phase)

        try:
            if phase == PhaseType.DISCOVERY:
                await self._run_discovery(round_num, result)
            elif phase == PhaseType.GENERATION:
                await self._run_generation(round_num, result)
            elif phase == PhaseType.OPTIMIZATION:
                await self._run_optimization(round_num, result)
            else:
                await self._run_validation(round_num, result)
        except Exception as e:
            logger.error("Round failed", round=round_num, error=str(e))
            result.errors.append(str(e))

        self._session.rounds.append(result)
        return result

    async def _run_discovery(self, round_num: int, result: RoundResult) -> None:
        """Discovery 단계: 전략 아이디어 생성"""
        # 프롬프트 생성
        prompt = self._prompt_builder.build_prompt(self._session, round_num)

        # OpenAI 호출
        response = await self._call_openai(prompt)
        self._session.total_openai_calls += 1

        if not response:
            result.errors.append("Empty OpenAI response")
            return

        result.openai_analysis = response.get("market_analysis", "")

        # 아이디어 → StrategyCandidate 생성
        ideas = response.get("ideas", [])
        for idea in ideas:
            candidate_id = f"c_{uuid.uuid4().hex[:8]}"

            strategy_type = self._parse_strategy_type(idea.get("type", "mean_reversion"))

            candidate = StrategyCandidate(
                candidate_id=candidate_id,
                name=idea.get("name", f"Strategy_{candidate_id}"),
                strategy_type=strategy_type,
                description=idea.get("description", ""),
                parameters=idea.get("expected_params", {}),
                param_count=len(idea.get("expected_params", {})),
                phase=PhaseType.DISCOVERY,
                created_round=round_num,
            )

            self._session.candidates[candidate_id] = candidate
            result.candidates_created.append(candidate_id)

        logger.info(
            "Discovery complete",
            ideas_count=len(ideas),
            total_candidates=len(self._session.candidates),
        )

    async def _run_generation(self, round_num: int, result: RoundResult) -> None:
        """Generation 단계: 코드 생성 + 빠른 백테스트"""
        # 코드 없는 후보 우선, 없으면 성능 낮은 것
        unimplemented = [
            c for c in self._session.get_active_candidates()
            if not c.signal_code
        ]

        if not unimplemented:
            # 성능 낮은 후보 개선
            implemented = sorted(
                self._session.get_active_candidates(),
                key=lambda c: c.return_pct,
            )
            unimplemented = implemented[:1]

        error_feedback = None

        for candidate in unimplemented[:2]:  # 라운드당 최대 2개
            for retry in range(MAX_CODE_RETRIES + 1):
                # 프롬프트 생성
                summaries = await self._get_backtest_summaries(quick=True)
                prompt = self._prompt_builder.build_prompt(
                    self._session, round_num,
                    backtest_summaries=summaries,
                    error_feedback=error_feedback,
                )

                # OpenAI 코드 생성
                response = await self._call_openai(prompt)
                self._session.total_openai_calls += 1

                if not response:
                    result.errors.append("Empty code generation response")
                    break

                result.openai_analysis = response.get("reasoning", "")

                # 코드 추출 및 컴파일
                candidate.signal_code = response.get("signal_code", "")
                candidate.exit_code = response.get("exit_code", "")

                if response.get("parameters"):
                    candidate.parameters = response["parameters"]
                    candidate.param_count = len(candidate.parameters)

                candidate.last_modified_round = round_num

                # 컴파일 + smoke test
                signal_func, exit_func, errors = self._codegen.compile_strategy(candidate)

                if errors:
                    error_feedback = self._codegen.format_error_for_retry(candidate, errors)
                    logger.warning(
                        "Code compilation failed",
                        candidate=candidate.name,
                        retry=retry,
                        errors=errors,
                    )
                    if retry == MAX_CODE_RETRIES:
                        result.errors.extend(errors)
                        candidate.is_active = False
                    continue

                # 컴파일 성공 → 빠른 백테스트 (30일, 20심볼)
                metrics = await self._run_quick_backtest(
                    candidate.candidate_id, candidate.name,
                    signal_func, exit_func,
                )

                if metrics:
                    self._update_candidate_metrics(candidate, metrics)
                    result.candidates_modified.append(candidate.candidate_id)

                    if metrics["return_pct"] > result.best_return_pct:
                        result.best_return_pct = metrics["return_pct"]
                        result.best_candidate_id = candidate.candidate_id

                logger.info(
                    "Strategy generated",
                    candidate=candidate.name,
                    return_pct=f"{candidate.return_pct:+.2f}%",
                    trades=candidate.total_trades,
                )
                break  # 성공 → 재시도 불필요

    async def _run_optimization(self, round_num: int, result: RoundResult) -> None:
        """Optimization 단계: 파라미터 최적화 + 전체 백테스트"""
        # 상위 5개 후보
        top_5 = self._session.get_top_candidates(5)

        if not top_5:
            result.errors.append("No candidates to optimize")
            return

        # 전체 백테스트로 정확한 성능 측정
        for candidate in top_5:
            # 매번 새 인스턴스 컴파일 (내부 상태 초기화)
            signal_func, exit_func, errors = self._fresh_compile(candidate)
            if errors:
                continue

            # 전체 백테스트 (3개월, 70심볼)
            metrics = await self._run_full_backtest(
                candidate.candidate_id, candidate.name,
                signal_func, exit_func,
            )
            if metrics:
                self._update_candidate_metrics(candidate, metrics)

        # 백테스트 요약 수집
        summaries = await self._get_backtest_summaries(quick=False)

        # OpenAI에 최적화 요청
        prompt = self._prompt_builder.build_prompt(
            self._session, round_num,
            backtest_summaries=summaries,
        )

        response = await self._call_openai(prompt)
        self._session.total_openai_calls += 1

        if not response:
            return

        result.openai_analysis = response.get("analysis", "")

        # 최적화 적용
        optimizations = response.get("optimizations", [])
        for opt in optimizations:
            cid = opt.get("candidate_id", "")
            candidate = self._session.candidates.get(cid)
            if not candidate:
                continue

            # 기존 백업
            old_params = deepcopy(candidate.parameters)
            old_return = candidate.return_pct

            # 새 파라미터 적용
            new_params = opt.get("new_params", {})
            if new_params:
                candidate.parameters = new_params
                candidate.param_count = len(new_params)

            # 코드 변경이 있으면 적용
            code_changes = opt.get("code_changes")
            if code_changes and code_changes != "null":
                candidate.signal_code = code_changes

            candidate.last_modified_round = round_num

            # 재컴파일 + 백테스트 (새 인스턴스)
            signal_func, exit_func, errors = self._fresh_compile(candidate)
            if errors:
                # 롤백
                candidate.parameters = old_params
                candidate.param_count = len(old_params)
                result.errors.extend(errors)
                continue

            metrics = await self._run_full_backtest(
                candidate.candidate_id, candidate.name,
                signal_func, exit_func,
            )

            if metrics and metrics["return_pct"] > old_return:
                # 개선 → 유지
                self._update_candidate_metrics(candidate, metrics)
                result.candidates_modified.append(cid)
                logger.info(
                    "Optimization improved",
                    candidate=candidate.name,
                    before=f"{old_return:+.2f}%",
                    after=f"{metrics['return_pct']:+.2f}%",
                )
            else:
                # 악화 → 롤백
                candidate.parameters = old_params
                candidate.param_count = len(old_params)
                if metrics:
                    logger.info(
                        "Optimization reverted",
                        candidate=candidate.name,
                        before=f"{old_return:+.2f}%",
                        after=f"{metrics['return_pct']:+.2f}%",
                    )

        # 최고 성과 기록
        top = self._session.get_top_candidates(1)
        if top:
            result.best_return_pct = top[0].return_pct
            result.best_candidate_id = top[0].candidate_id

    async def _run_validation(self, round_num: int, result: RoundResult) -> None:
        """Validation 단계: Walk-forward 검증 + 앙상블"""
        top_candidates = self._session.get_top_candidates(10)

        if not top_candidates:
            result.errors.append("No candidates to validate")
            return

        # Walk-forward 검증
        wf_validator = WalkForwardValidator(
            self._session.symbols,
            self._session.interval,
            self._session.initial_capital,
        )

        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=90)

        for candidate in top_candidates:
            if not candidate.signal_code:
                continue

            try:
                wf_result = await wf_validator.validate(
                    candidate, candidate.name,
                    start_date, end_date,
                    train_days=60, test_days=30,
                )

                candidate.wf_oos_return = wf_result.avg_oos_return
                candidate.wf_oos_sharpe = wf_result.avg_oos_sharpe
                candidate.wf_is_oos_ratio = wf_result.oos_is_ratio
                candidate.wf_passed = wf_result.passed

                logger.info(
                    "Walk-forward result",
                    candidate=candidate.name,
                    oos_return=f"{wf_result.avg_oos_return:+.2f}%",
                    passed=wf_result.passed,
                )
            except Exception as e:
                logger.error("Walk-forward failed", candidate=candidate.name, error=str(e))
                result.errors.append(f"WF failed for {candidate.name}: {e}")

        # OpenAI에 앙상블 분석 요청
        summaries = await self._get_backtest_summaries(quick=False)
        prompt = self._prompt_builder.build_prompt(
            self._session, round_num,
            backtest_summaries=summaries,
        )

        response = await self._call_openai(prompt)
        self._session.total_openai_calls += 1

        if response:
            result.openai_analysis = response.get("validation_analysis", "")

            # 앙상블 멤버 업데이트
            ensemble = response.get("ensemble", [])
            self._session.best_candidates = [
                e.get("candidate_id", "") for e in ensemble
                if e.get("candidate_id") in self._session.candidates
            ]

            # 최종 파라미터 적용
            for e in ensemble:
                cid = e.get("candidate_id", "")
                candidate = self._session.candidates.get(cid)
                if candidate and e.get("final_params"):
                    candidate.parameters = e["final_params"]
                    candidate.param_count = len(e["final_params"])

    async def _run_quick_backtest(
        self, candidate_id: str, name: str,
        signal_func: callable, exit_func: Optional[callable],
    ) -> Optional[dict]:
        """빠른 백테스트 (30일, 20심볼)"""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)

        return await self._run_backtest(
            name, signal_func, exit_func,
            QUICK_SYMBOLS, start_date, end_date,
        )

    async def _run_full_backtest(
        self, candidate_id: str, name: str,
        signal_func: callable, exit_func: Optional[callable],
    ) -> Optional[dict]:
        """전체 백테스트 (3개월, 전체 심볼)"""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=90)

        return await self._run_backtest(
            name, signal_func, exit_func,
            self._session.symbols, start_date, end_date,
        )

    async def _run_backtest(
        self, name: str,
        signal_func: callable,
        exit_func: Optional[callable],
        symbols: list[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[dict]:
        """백테스트 실행 (매번 새 DB 세션 사용)"""
        config = BacktestConfig(
            strategy=name,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            interval=self._session.interval,
            initial_capital=self._session.initial_capital,
        )

        self._session.total_backtests_run += 1

        try:
            async with async_session() as db_session:
                data_loader = BacktestDataLoader(db_session)
                engine = BacktestEngine(config, data_loader)
                result = await engine.run(signal_func, exit_func)
                return {
                    "return_pct": result.metrics.total_return_pct,
                    "sharpe": result.metrics.sharpe_ratio or 0,
                    "max_dd": result.metrics.max_drawdown_pct,
                    "win_rate": result.metrics.win_rate,
                    "profit_factor": result.metrics.profit_factor or 0,
                    "total_trades": result.metrics.total_trades,
                    "avg_win_pct": result.metrics.avg_win_pct or 0,
                    "avg_loss_pct": result.metrics.avg_loss_pct or 0,
                    "avg_holding_min": result.metrics.avg_holding_minutes or 0,
                    "max_consecutive_losses": result.metrics.max_consecutive_losses,
                    "trades": result.trades,
                }
        except Exception as e:
            logger.error("Backtest failed", strategy=name, error=str(e))
            return None

    def _update_candidate_metrics(self, candidate: StrategyCandidate, metrics: dict) -> None:
        """후보 메트릭 업데이트"""
        candidate.return_pct = metrics.get("return_pct", 0)
        candidate.sharpe = metrics.get("sharpe", 0)
        candidate.max_dd = metrics.get("max_dd", 0)
        candidate.win_rate = metrics.get("win_rate", 0)
        candidate.profit_factor = metrics.get("profit_factor", 0)
        candidate.total_trades = metrics.get("total_trades", 0)
        candidate.avg_holding_min = metrics.get("avg_holding_min", 0)

    async def _get_backtest_summaries(
        self, quick: bool = True
    ) -> dict[str, BacktestSummary]:
        """활성 후보들의 백테스트 요약 수집"""
        summaries = {}

        for candidate in self._session.get_active_candidates():
            if candidate.total_trades == 0:
                continue

            summary = BacktestSummary(
                return_pct=candidate.return_pct,
                sharpe=candidate.sharpe,
                max_dd=candidate.max_dd,
                win_rate=candidate.win_rate,
                profit_factor=candidate.profit_factor,
                total_trades=candidate.total_trades,
                avg_holding_min=candidate.avg_holding_min,
            )

            summaries[candidate.candidate_id] = summary

        return summaries

    async def _call_openai(self, prompt: str) -> Optional[dict]:
        """OpenAI API 호출"""
        try:
            response = await self._client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": self._prompt_builder.build_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4000,
            )

            text = response.choices[0].message.content.strip()

            # 비용 추정
            usage = response.usage
            if usage:
                # GPT-4o: $2.50/1M input, $10/1M output
                cost = (usage.prompt_tokens * 2.5 + usage.completion_tokens * 10) / 1_000_000
                self._session.total_cost_usd += cost

            # JSON 추출
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            return json.loads(text)

        except json.JSONDecodeError as e:
            logger.error("Failed to parse OpenAI response", error=str(e))
            return None
        except Exception as e:
            logger.error("OpenAI API call failed", error=str(e))
            return None

    @staticmethod
    def _parse_strategy_type(type_str: str) -> StrategyTypeAI:
        """문자열 → StrategyTypeAI"""
        try:
            return StrategyTypeAI(type_str.lower())
        except ValueError:
            return StrategyTypeAI.MEAN_REVERSION
