"""
异常熔断：ASR / 翻译连续失败时短暂拒绝请求，避免雪崩。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    reset_timeout_sec: float = 30.0
    _failures: int = 0
    _opened_at: float = 0.0

    def is_open(self) -> bool:
        if self._failures < self.failure_threshold:
            return False
        if time.time() - self._opened_at >= self.reset_timeout_sec:
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.time()

    def status(self) -> dict:
        return {
            "name": self.name,
            "open": self.is_open(),
            "failures": self._failures,
        }


_asr_breaker = CircuitBreaker("asr")
_llm_breaker = CircuitBreaker("llm")


def get_asr_breaker() -> CircuitBreaker:
    return _asr_breaker


def get_llm_breaker() -> CircuitBreaker:
    return _llm_breaker
