"""AI 생성 코드 샌드박스

AST 기반 검증으로 위험한 코드 차단, 타임아웃 실행, smoke test.
"""

import ast
import signal
import textwrap
import traceback
from typing import Optional

import structlog

logger = structlog.get_logger()

# 허용된 모듈
ALLOWED_IMPORTS = frozenset({
    "math", "datetime", "dataclasses", "typing", "collections",
    "statistics", "functools", "itertools", "enum", "copy",
})

# 차단된 이름들
BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "open", "__import__",
    "globals", "locals", "getattr", "setattr", "delattr",
    "breakpoint", "exit", "quit", "input", "print",
})

BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "pathlib",
    "socket", "http", "urllib", "requests", "httpx",
    "asyncio", "threading", "multiprocessing",
    "pickle", "shelve", "sqlite3", "sqlalchemy",
    "importlib", "ctypes", "signal",
})


class SandboxError(Exception):
    """샌드박스 검증 실패"""
    pass


class TimeoutError(Exception):
    """실행 타임아웃"""
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError("Execution timed out (30s)")


class CodeSandbox:
    """AI 생성 코드 안전 실행 샌드박스"""

    def __init__(self, timeout_sec: int = 30):
        self.timeout_sec = timeout_sec

    def validate_code(self, code: str) -> list[str]:
        """AST 기반 코드 검증. 위반 사항 리스트 반환 (빈 리스트 = 통과)."""
        violations = []

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [f"SyntaxError: {e}"]

        for node in ast.walk(tree):
            # Import 검증
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in BLOCKED_MODULES:
                        violations.append(f"Blocked import: {alias.name}")
                    elif module not in ALLOWED_IMPORTS:
                        violations.append(f"Unapproved import: {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module in BLOCKED_MODULES:
                        violations.append(f"Blocked import from: {node.module}")
                    elif module not in ALLOWED_IMPORTS:
                        violations.append(f"Unapproved import from: {node.module}")

            # 위험한 builtin 호출
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in BLOCKED_BUILTINS:
                    violations.append(f"Blocked builtin: {func.id}()")
                elif isinstance(func, ast.Attribute):
                    if func.attr in ("system", "popen", "exec", "eval"):
                        violations.append(f"Blocked method: .{func.attr}()")

            # __dunder__ 접근 차단 (일부 허용)
            elif isinstance(node, ast.Attribute):
                if (
                    node.attr.startswith("__")
                    and node.attr.endswith("__")
                    and node.attr not in ("__init__", "__call__", "__len__", "__iter__", "__next__", "__str__", "__repr__")
                ):
                    violations.append(f"Blocked dunder access: {node.attr}")

        return violations

    def compile_code(self, code: str, name: str = "<ai_strategy>") -> Optional[object]:
        """코드 컴파일 및 네임스페이스 반환"""
        violations = self.validate_code(code)
        if violations:
            raise SandboxError(f"Code validation failed:\n" + "\n".join(violations))

        namespace = {
            "__builtins__": {
                k: v for k, v in __builtins__.items()
                if isinstance(__builtins__, dict)
            } if isinstance(__builtins__, dict) else {
                k: getattr(__builtins__, k) for k in dir(__builtins__)
                if not k.startswith("_")
            },
        }

        # 안전한 builtins만 허용
        safe_builtins = {
            "abs", "all", "any", "bool", "dict", "enumerate", "filter",
            "float", "frozenset", "int", "isinstance", "issubclass",
            "len", "list", "map", "max", "min", "None", "True", "False",
            "range", "reversed", "round", "set", "slice", "sorted",
            "str", "sum", "tuple", "type", "zip", "ValueError",
            "TypeError", "KeyError", "IndexError", "RuntimeError",
            "Exception", "StopIteration", "property", "staticmethod",
            "classmethod", "super", "object", "hasattr",
        }
        import builtins as _builtins
        safe_dict = {
            k: getattr(_builtins, k) for k in safe_builtins
            if hasattr(_builtins, k)
        }

        # 제한된 __import__ (허용된 모듈만)
        import math
        import datetime
        import dataclasses
        import typing
        import collections
        import statistics
        import functools
        import itertools
        import copy

        _safe_modules = {
            "math": math, "datetime": datetime, "dataclasses": dataclasses,
            "typing": typing, "collections": collections,
            "statistics": statistics, "functools": functools,
            "itertools": itertools, "copy": copy,
        }

        def _restricted_import(name, *args, **kwargs):
            top = name.split(".")[0]
            if top in _safe_modules:
                return _safe_modules[top]
            raise ImportError(f"Import of '{name}' is not allowed in sandbox")

        safe_dict["__import__"] = _restricted_import
        # __build_class__ is required for class definitions
        safe_dict["__build_class__"] = _builtins.__build_class__
        # __name__ needed for some class mechanisms
        safe_dict["__name__"] = "__sandbox__"
        namespace["__builtins__"] = safe_dict

        # math, datetime 기본 제공
        namespace["math"] = math
        namespace["datetime"] = datetime

        try:
            compiled = compile(code, name, "exec")
            exec(compiled, namespace)
        except Exception as e:
            raise SandboxError(f"Compilation/execution error: {e}")

        return namespace

    def smoke_test(
        self,
        signal_func: callable,
        exit_func: Optional[callable],
        timeout_sec: Optional[int] = None,
    ) -> tuple[bool, str]:
        """
        Mock HistoryProvider로 기본 호출 테스트.
        Returns: (passed, error_message)
        """
        timeout = timeout_sec or self.timeout_sec

        # Mock HistoryProvider
        mock_hp = _MockHistoryProvider()
        mock_symbols = ["KRW-BTC", "KRW-ETH", "KRW-DOGE"]
        mock_ts = datetime_module().datetime(2025, 1, 15, 12, 0, 0)

        def _run_test():
            # signal_generator 테스트
            signals = signal_func(mock_ts, mock_hp, mock_symbols)
            if not isinstance(signals, list):
                return False, f"signal_generator must return list, got {type(signals)}"

            for sig in signals:
                if not isinstance(sig, dict):
                    return False, f"Signal must be dict, got {type(sig)}"
                if "symbol" not in sig or "action" not in sig:
                    return False, "Signal must have 'symbol' and 'action' keys"

            # exit_checker 테스트
            if exit_func:
                mock_pos = _MockPosition()
                result = exit_func(mock_pos, 50000.0, mock_hp)
                if result is not None and not isinstance(result, str):
                    return False, f"exit_checker must return str or None, got {type(result)}"

            return True, ""

        # 타임아웃 설정
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)
        try:
            passed, error = _run_test()
            return passed, error
        except TimeoutError:
            return False, f"Smoke test timed out ({timeout}s)"
        except Exception as e:
            return False, f"Smoke test error: {traceback.format_exc()}"
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)


def datetime_module():
    """datetime 모듈 안전 접근"""
    import datetime
    return datetime


class _MockHistoryProvider:
    """Smoke test용 Mock HistoryProvider"""

    def get_candles(self, symbol: str, interval: str, count: int = 50) -> list:
        """가짜 캔들 데이터 생성"""
        import datetime as dt
        base_price = {"KRW-BTC": 50000000, "KRW-ETH": 3000000}.get(symbol, 1000)
        base_time = dt.datetime(2025, 1, 15, 0, 0)
        candles = []
        for i in range(count):
            price = base_price * (1 + (i % 10 - 5) * 0.001)
            candle_time = base_time + dt.timedelta(minutes=i * 5)
            candles.append(_MockCandle(
                symbol=symbol,
                interval=interval,
                open_time=candle_time,
                open=price * 0.999,
                high=price * 1.002,
                low=price * 0.997,
                close=price,
                volume=1000.0 + i * 10,
                quote_volume=price * (1000.0 + i * 10),
            ))
        return candles

    def get_closes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        return [c.close for c in self.get_candles(symbol, interval, count)]

    def get_highs(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        return [c.high for c in self.get_candles(symbol, interval, count)]

    def get_lows(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        return [c.low for c in self.get_candles(symbol, interval, count)]

    def get_volumes(self, symbol: str, interval: str, count: int = 50) -> list[float]:
        return [c.quote_volume for c in self.get_candles(symbol, interval, count)]

    def get_current_price(self, symbol: str, interval: str = "5m") -> Optional[float]:
        candles = self.get_candles(symbol, interval, 1)
        return candles[-1].close if candles else None

    def calc_sma(self, symbol: str, interval: str, period: int) -> Optional[float]:
        closes = self.get_closes(symbol, interval, period)
        return sum(closes) / len(closes) if closes else None

    def calc_ema(self, symbol: str, interval: str, period: int) -> Optional[float]:
        return self.calc_sma(symbol, interval, period)

    def calc_rvol(self, symbol: str, interval: str, period: int = 20) -> Optional[float]:
        return 1.5

    def calc_atr(self, symbol: str, interval: str, period: int = 14) -> Optional[float]:
        price = self.get_current_price(symbol, interval)
        return price * 0.012 if price else None


class _MockCandle:
    """Mock 캔들 데이터"""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _MockPosition:
    """Mock 포지션"""
    def __init__(self):
        import datetime as dt
        self.symbol = "KRW-BTC"
        self.strategy = "test"
        self.entry_price = 50000000
        self.entry_time = dt.datetime(2025, 1, 15, 11, 0, 0)
        self.max_profit_pct = 0.005
        self.max_drawdown_pct = -0.002
        self.quantity = 0.001
        self.trade_id = "test-001"
