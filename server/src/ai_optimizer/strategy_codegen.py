"""전략 코드 생성기

AI 코드 → sandbox 검증 → 컴파일 → smoke test → BacktestEngine 인터페이스 래핑
실패 시 에러 메시지를 GPT-4o에 피드백 (최대 2회 재시도)
"""

import json
import textwrap
import traceback
from typing import Optional

import structlog

from src.ai_optimizer.sandbox import CodeSandbox, SandboxError
from src.ai_optimizer.schemas import StrategyCandidate

logger = structlog.get_logger()

MAX_RETRIES = 2


class CodeGenerationError(Exception):
    """코드 생성 실패"""
    pass


class StrategyCodegen:
    """AI 생성 전략 코드를 BacktestEngine 호환 인터페이스로 래핑"""

    def __init__(self):
        self.sandbox = CodeSandbox(timeout_sec=30)

    def compile_strategy(
        self,
        candidate: StrategyCandidate,
    ) -> tuple[Optional[callable], Optional[callable], list[str]]:
        """
        후보 전략의 코드를 컴파일하고 callable 반환.

        Returns:
            (signal_generator, exit_checker, errors)
            성공 시 errors=[], 실패 시 signal_generator=None
        """
        errors = []

        # 1. signal_code 컴파일
        signal_func = None
        if candidate.signal_code:
            try:
                signal_func = self._compile_signal(candidate)
            except (SandboxError, CodeGenerationError) as e:
                errors.append(f"Signal compile error: {e}")

        # 2. exit_code 컴파일
        exit_func = None
        if candidate.exit_code:
            try:
                exit_func = self._compile_exit(candidate)
            except (SandboxError, CodeGenerationError) as e:
                errors.append(f"Exit compile error: {e}")
        else:
            # 기본 exit checker 사용
            exit_func = self._create_default_exit(candidate.parameters)

        # 3. Smoke test
        if signal_func and not errors:
            passed, smoke_error = self.sandbox.smoke_test(signal_func, exit_func)
            if not passed:
                errors.append(f"Smoke test failed: {smoke_error}")
                signal_func = None

        candidate.compile_errors = errors
        return signal_func, exit_func, errors

    def _compile_signal(self, candidate: StrategyCandidate) -> callable:
        """시그널 생성기 컴파일"""
        code = candidate.signal_code.strip()

        # 코드에서 클래스 이름 추출
        class_name = self._extract_class_name(code)
        if not class_name:
            # 함수형이면 직접 래핑
            func_name = self._extract_func_name(code)
            if func_name:
                return self._compile_function_signal(code, func_name, candidate)
            raise CodeGenerationError("Cannot find class or function in signal code")

        # 클래스형 컴파일
        namespace = self.sandbox.compile_code(code, f"<signal_{candidate.candidate_id}>")

        cls = namespace.get(class_name)
        if cls is None:
            raise CodeGenerationError(f"Class '{class_name}' not found after compilation")

        # 인스턴스 생성
        try:
            instance = cls(candidate.parameters)
        except TypeError:
            try:
                instance = cls()
            except Exception as e:
                raise CodeGenerationError(f"Cannot instantiate {class_name}: {e}")

        # callable 검증
        if not callable(instance):
            raise CodeGenerationError(f"{class_name} instance is not callable")

        return instance

    def _compile_function_signal(
        self, code: str, func_name: str, candidate: StrategyCandidate
    ) -> callable:
        """함수형 시그널 코드 컴파일"""
        namespace = self.sandbox.compile_code(code, f"<signal_{candidate.candidate_id}>")

        func = namespace.get(func_name)
        if func is None:
            raise CodeGenerationError(f"Function '{func_name}' not found")

        # 함수를 BacktestEngine 인터페이스로 래핑
        params = candidate.parameters

        def wrapped_signal(timestamp, history, symbols):
            return func(timestamp, history, symbols, params)

        return wrapped_signal

    def _compile_exit(self, candidate: StrategyCandidate) -> callable:
        """청산 체커 컴파일"""
        code = candidate.exit_code.strip()

        func_name = self._extract_func_name(code)
        if not func_name:
            raise CodeGenerationError("Cannot find function in exit code")

        namespace = self.sandbox.compile_code(code, f"<exit_{candidate.candidate_id}>")

        factory = namespace.get(func_name)
        if factory is None:
            raise CodeGenerationError(f"Function '{func_name}' not found")

        # 팩토리 호출로 exit_checker 생성
        try:
            exit_checker = factory(**self._extract_exit_params(candidate.parameters))
        except TypeError:
            try:
                exit_checker = factory()
            except Exception as e:
                raise CodeGenerationError(f"Cannot call exit factory: {e}")

        return exit_checker

    def _create_default_exit(self, params: dict) -> callable:
        """기본 exit checker (파라미터 기반)"""
        from src.backtesting.engine.backtest_engine import create_pullback_exit_checker

        return create_pullback_exit_checker(
            stop_loss_pct=params.get("stop_loss_pct", -0.02),
            take_profit_pct=params.get("take_profit_pct", 0.02),
            trailing_trigger_pct=params.get("trailing_trigger_pct", 0.02),
            trailing_stop_pct=params.get("trailing_stop_pct", 0.01),
            time_stop_minutes=params.get("time_stop_minutes", 180),
        )

    def _extract_exit_params(self, params: dict) -> dict:
        """exit checker에 필요한 파라미터만 추출"""
        exit_keys = {
            "stop_loss_pct", "take_profit_pct",
            "trailing_trigger_pct", "trailing_stop_pct",
            "time_stop_minutes",
        }
        return {k: v for k, v in params.items() if k in exit_keys}

    @staticmethod
    def _extract_class_name(code: str) -> Optional[str]:
        """코드에서 첫 번째 클래스명 추출"""
        import ast
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    return node.name
        except SyntaxError:
            pass
        return None

    @staticmethod
    def _extract_func_name(code: str) -> Optional[str]:
        """코드에서 첫 번째 함수명 추출"""
        import ast
        try:
            tree = ast.parse(code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef):
                    return node.name
        except SyntaxError:
            pass
        return None

    def format_error_for_retry(
        self,
        candidate: StrategyCandidate,
        errors: list[str],
    ) -> str:
        """재시도용 에러 피드백 텍스트 생성"""
        return (
            f"Strategy '{candidate.name}' code compilation failed.\n"
            f"Errors:\n" + "\n".join(f"  - {e}" for e in errors) + "\n\n"
            f"Please fix the code. Common issues:\n"
            f"- Use ONLY HistoryProvider API methods\n"
            f"- signal_generator must return list[dict] with keys: symbol, action, strategy, score, position_pct\n"
            f"- exit_checker must return str (reason) or None\n"
            f"- No imports except math, datetime, dataclasses, typing\n"
            f"- Include cooldown logic with self._last_entry dict\n"
        )
