"""Hysteresis Tracker for Capital Profile Mode Transitions

히스테리시스 기반 모드 전환 추적기:
- Preserve 진입: Equity >= 10,000,000 KRW를 3일 연속 유지
- Growth 복귀: Equity <= 9,000,000 KRW를 2일 연속 유지

이를 통해 자산이 기준선 근처에서 진동할 때 모드가 불안정하게 바뀌는 것을 방지합니다.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class CapitalMode(str, Enum):
    """자본 프로필 모드"""
    GROWTH = "GROWTH"      # 자산 < 10M: 공격적 성장
    PRESERVE = "PRESERVE"  # 자산 >= 10M: 수익 보존


@dataclass
class DailyEquityRecord:
    """일별 Equity 기록"""
    date: str  # YYYY-MM-DD
    equity: float
    mode_at_day_end: str
    meets_preserve_threshold: bool  # >= threshold
    meets_growth_threshold: bool    # <= lower threshold


@dataclass
class HysteresisState:
    """히스테리시스 상태"""
    current_mode: CapitalMode = CapitalMode.GROWTH
    consecutive_preserve_days: int = 0
    consecutive_growth_days: int = 0
    last_transition_date: Optional[str] = None
    last_transition_reason: Optional[str] = None
    history: list[DailyEquityRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return {
            "current_mode": self.current_mode.value,
            "consecutive_preserve_days": self.consecutive_preserve_days,
            "consecutive_growth_days": self.consecutive_growth_days,
            "last_transition_date": self.last_transition_date,
            "last_transition_reason": self.last_transition_reason,
            "history": [asdict(h) for h in self.history[-30:]],  # 최근 30일만 저장
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HysteresisState":
        """딕셔너리에서 복원"""
        history = [
            DailyEquityRecord(**h) for h in data.get("history", [])
        ]
        return cls(
            current_mode=CapitalMode(data.get("current_mode", "GROWTH")),
            consecutive_preserve_days=data.get("consecutive_preserve_days", 0),
            consecutive_growth_days=data.get("consecutive_growth_days", 0),
            last_transition_date=data.get("last_transition_date"),
            last_transition_reason=data.get("last_transition_reason"),
            history=history,
        )


class HysteresisTracker:
    """히스테리시스 기반 모드 전환 추적기"""

    def __init__(
        self,
        threshold_krw: float = None,
        lower_threshold_krw: float = None,
        preserve_days: int = None,
        growth_days: int = None,
        state_file: str = None,
    ):
        """
        Args:
            threshold_krw: Preserve 모드 전환 기준 (기본: 10,000,000 KRW)
            lower_threshold_krw: Growth 모드 복귀 기준 (기본: 9,000,000 KRW)
            preserve_days: Preserve 진입 연속 일수 (기본: 3일)
            growth_days: Growth 복귀 연속 일수 (기본: 2일)
            state_file: 상태 저장 파일 경로
        """
        self.threshold_krw = threshold_krw or settings.capital_profile_threshold_krw
        self.lower_threshold_krw = lower_threshold_krw or settings.capital_profile_growth_lower_krw
        self.preserve_days = preserve_days or settings.capital_profile_preserve_days
        self.growth_days = growth_days or settings.capital_profile_growth_days

        # 상태 파일 경로
        self.state_file = Path(state_file or "data/capital_profile_state.json")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # 상태 로드 또는 초기화
        self.state = self._load_state()

        logger.info(
            "HysteresisTracker initialized",
            threshold_krw=self.threshold_krw,
            lower_threshold_krw=self.lower_threshold_krw,
            preserve_days=self.preserve_days,
            growth_days=self.growth_days,
            current_mode=self.state.current_mode.value,
        )

    def _load_state(self) -> HysteresisState:
        """상태 파일에서 로드"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                state = HysteresisState.from_dict(data)
                logger.info(
                    "Loaded hysteresis state",
                    mode=state.current_mode.value,
                    consecutive_preserve=state.consecutive_preserve_days,
                    consecutive_growth=state.consecutive_growth_days,
                )
                return state
            except Exception as e:
                logger.warning("Failed to load state, starting fresh", error=str(e))

        return HysteresisState()

    def _save_state(self) -> None:
        """상태 파일에 저장"""
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save state", error=str(e))

    def get_current_mode(self) -> CapitalMode:
        """현재 모드 반환"""
        return self.state.current_mode

    def get_state(self) -> HysteresisState:
        """전체 상태 반환"""
        return self.state

    def record_daily_equity(
        self,
        equity: float,
        record_date: date = None,
    ) -> tuple[CapitalMode, bool, str]:
        """
        일별 Equity 기록 및 모드 전환 체크

        Args:
            equity: 당일 종가 기준 Equity (KRW)
            record_date: 기록 날짜 (기본: 오늘)

        Returns:
            (현재 모드, 전환 여부, 전환 사유)
        """
        record_date = record_date or date.today()
        date_str = record_date.isoformat()

        # 이미 오늘 기록이 있으면 업데이트
        existing = next(
            (h for h in self.state.history if h.date == date_str),
            None
        )

        meets_preserve = equity >= self.threshold_krw
        meets_growth = equity <= self.lower_threshold_krw

        record = DailyEquityRecord(
            date=date_str,
            equity=equity,
            mode_at_day_end=self.state.current_mode.value,
            meets_preserve_threshold=meets_preserve,
            meets_growth_threshold=meets_growth,
        )

        if existing:
            # 기존 기록 업데이트
            idx = self.state.history.index(existing)
            self.state.history[idx] = record
        else:
            # 새 기록 추가
            self.state.history.append(record)

        # 히스토리 최대 30일 유지
        if len(self.state.history) > 30:
            self.state.history = self.state.history[-30:]

        # 연속 일수 업데이트
        self._update_consecutive_days()

        # 모드 전환 체크
        transitioned, new_mode, reason = self._check_transition()

        if transitioned:
            old_mode = self.state.current_mode
            self.state.current_mode = new_mode
            self.state.last_transition_date = date_str
            self.state.last_transition_reason = reason

            logger.info(
                "Capital Profile mode transition",
                old_mode=old_mode.value,
                new_mode=new_mode.value,
                equity=equity,
                reason=reason,
            )

        # 상태 저장
        self._save_state()

        return self.state.current_mode, transitioned, reason

    def _update_consecutive_days(self) -> None:
        """연속 일수 계산"""
        # 최근 기록부터 역순으로 확인
        preserve_count = 0
        growth_count = 0

        for record in reversed(self.state.history):
            # Preserve 연속 일수
            if record.meets_preserve_threshold:
                preserve_count += 1
            else:
                break

        for record in reversed(self.state.history):
            # Growth 연속 일수
            if record.meets_growth_threshold:
                growth_count += 1
            else:
                break

        self.state.consecutive_preserve_days = preserve_count
        self.state.consecutive_growth_days = growth_count

    def _check_transition(self) -> tuple[bool, CapitalMode, str]:
        """
        모드 전환 조건 체크

        Returns:
            (전환 여부, 새 모드, 전환 사유)
        """
        current_mode = self.state.current_mode

        # Growth → Preserve 전환: 3일 연속 >= threshold
        if current_mode == CapitalMode.GROWTH:
            if self.state.consecutive_preserve_days >= self.preserve_days:
                reason = f"Equity >= {self.threshold_krw:,.0f} KRW for {self.preserve_days} consecutive days"
                return True, CapitalMode.PRESERVE, reason

        # Preserve → Growth 복귀: 2일 연속 <= lower_threshold
        elif current_mode == CapitalMode.PRESERVE:
            if self.state.consecutive_growth_days >= self.growth_days:
                reason = f"Equity <= {self.lower_threshold_krw:,.0f} KRW for {self.growth_days} consecutive days"
                return True, CapitalMode.GROWTH, reason

        return False, current_mode, ""

    def check_instant_equity(self, equity: float) -> CapitalMode:
        """
        실시간 Equity로 현재 모드 확인 (일별 기록 없이)

        히스테리시스 조건은 적용하지 않고, 현재 저장된 모드만 반환합니다.
        모드 전환은 record_daily_equity()를 통해서만 발생합니다.

        Args:
            equity: 현재 Equity (KRW)

        Returns:
            현재 모드 (전환 없음)
        """
        return self.state.current_mode

    def force_mode(self, mode: CapitalMode, reason: str) -> None:
        """
        강제 모드 전환 (관리자용)

        Args:
            mode: 전환할 모드
            reason: 전환 사유
        """
        old_mode = self.state.current_mode
        self.state.current_mode = mode
        self.state.last_transition_date = date.today().isoformat()
        self.state.last_transition_reason = f"[FORCED] {reason}"

        # 연속 일수 초기화
        self.state.consecutive_preserve_days = 0
        self.state.consecutive_growth_days = 0

        self._save_state()

        logger.warning(
            "Capital Profile mode FORCED",
            old_mode=old_mode.value,
            new_mode=mode.value,
            reason=reason,
        )

    def get_status_dict(self) -> dict:
        """API 응답용 상태 딕셔너리"""
        return {
            "current_mode": self.state.current_mode.value,
            "consecutive_preserve_days": self.state.consecutive_preserve_days,
            "consecutive_growth_days": self.state.consecutive_growth_days,
            "preserve_threshold_krw": self.threshold_krw,
            "growth_threshold_krw": self.lower_threshold_krw,
            "preserve_days_required": self.preserve_days,
            "growth_days_required": self.growth_days,
            "last_transition_date": self.state.last_transition_date,
            "last_transition_reason": self.state.last_transition_reason,
            "days_until_preserve": max(0, self.preserve_days - self.state.consecutive_preserve_days),
            "days_until_growth": max(0, self.growth_days - self.state.consecutive_growth_days),
        }


# Singleton
_tracker: HysteresisTracker = None


def get_hysteresis_tracker() -> HysteresisTracker:
    """HysteresisTracker 싱글톤"""
    global _tracker
    if _tracker is None:
        _tracker = HysteresisTracker()
    return _tracker
