"""Token budget tracking for multi-layer context management."""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Dict, List, Optional

from dbgpt.model.utils.token_utils import ProxyTokenizerWrapper

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONTEXT_TOKENS = 120000

# 中文（含全角标点）在主流 tokenizer 下约 1 字符 ≈ 1 token。
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def _estimate_tokens(text: str) -> int:
    """字符类型感知的 token 估算，用于 tokenizer 不可用时的兜底（2026-09-26）。

    原实现是 ``len(text) // 4`` —— 按英文比例估的数。本系统的提示词以中文为主
    （现场实测 system prompt：12,388 个中文字符 + 35,414 个 ASCII 字符），中文
    按 //4 会被低估到约四分之一，于是"上下文已贴满窗口"却算成"只用了 12%"：
    压缩阈值永远达不到、压缩永不触发，而模型侧已经在超窗截断。

    这里按字符类型分治：CJK 约 1 字符/token，其余按 3.5 字符/token。用 tiktoken
    的 cl100k_base 对现场真实提示词（48,679 字符）校验：本估算 23,611 vs 实测
    25,586，误差 7.7%；原 ``len//4`` 估算为 12,169，误差 52.4%（低估 2.1 倍）。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    others = len(text) - cjk
    return cjk + int(others / 3.5 + 0.5)


class TokenState(Enum):
    """Context budget state levels."""

    NORMAL = "normal"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    OVERFLOW = "overflow"

    def __ge__(self, other: "TokenState") -> bool:
        order = list(TokenState)
        return order.index(self) >= order.index(other)

    def __gt__(self, other: "TokenState") -> bool:
        order = list(TokenState)
        return order.index(self) > order.index(other)

    def __le__(self, other: "TokenState") -> bool:
        order = list(TokenState)
        return order.index(self) <= order.index(other)

    def __lt__(self, other: "TokenState") -> bool:
        order = list(TokenState)
        return order.index(self) < order.index(other)


class ContextBudgetConfig:
    """Configuration for context budget management."""

    def __init__(
        self,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        warning_threshold: float = 0.70,
        error_threshold: float = 0.90,
        critical_threshold: float = 0.95,
        reserved_tokens: int = 4096,
        min_keep_recent_rounds: int = 3,
        max_compact_failures: int = 3,
        max_observation_age_rounds: int = 5,
        truncated_observation_max_chars: int = 500,
        min_keep_tokens: int = 10000,
        # Tool result persistence (Layer 2/3) configuration.
        tool_result_threshold: int = 100_000,
        turn_result_budget: int = 200_000,
        preview_size: int = 1500,
        tool_overrides: Optional[Dict[str, int]] = None,
    ):
        # `max_context_tokens <= 0` means "auto-detect from model metadata".
        # If the caller could not resolve metadata, keep the context window usable
        # by falling back to the system default budget instead of leaving it at 0.
        self.max_context_tokens = (
            max_context_tokens
            if max_context_tokens and max_context_tokens > 0
            else DEFAULT_MAX_CONTEXT_TOKENS
        )
        self.warning_threshold = warning_threshold
        self.error_threshold = error_threshold
        self.critical_threshold = critical_threshold
        self.reserved_tokens = reserved_tokens
        self.min_keep_recent_rounds = min_keep_recent_rounds
        self.max_compact_failures = max_compact_failures
        self.max_observation_age_rounds = max_observation_age_rounds
        self.truncated_observation_max_chars = truncated_observation_max_chars
        self.min_keep_tokens = min_keep_tokens
        # Tool result persistence config (Layer 2: per-result, Layer 3: per-turn).
        self.tool_result_threshold = tool_result_threshold
        self.turn_result_budget = turn_result_budget
        self.preview_size = preview_size
        self.tool_overrides: Dict[str, int] = tool_overrides or {}

    @property
    def effective_budget(self) -> int:
        """Max tokens available after reserving space for output."""
        return self.max_context_tokens - self.reserved_tokens


class ContextBudgetTracker:
    """Tracks token usage and determines context budget state."""

    def __init__(
        self,
        config: ContextBudgetConfig,
        model_name: Optional[str] = None,
    ):
        self.config = config
        self.model_name = model_name
        self._tokenizer = ProxyTokenizerWrapper()
        self._token_history: List[int] = []
        self._compact_failure_count: int = 0

    def count_messages(self, messages: list) -> int:
        """Count total tokens across a list of AgentMessage objects."""
        total = 0
        for msg in messages:
            content = getattr(msg, "content", None) or ""
            count = self._tokenizer.count_token(content, self.model_name)
            if count < 0:
                count = _estimate_tokens(content)
            total += count
        return total

    def get_state(self, token_count: int) -> TokenState:
        """Determine budget state based on current token count."""
        budget = self.config.effective_budget
        if budget <= 0:
            return TokenState.OVERFLOW

        ratio = token_count / budget
        if ratio >= 1.0:
            return TokenState.OVERFLOW
        elif ratio >= self.config.critical_threshold:
            return TokenState.CRITICAL
        elif ratio >= self.config.error_threshold:
            return TokenState.ERROR
        elif ratio >= self.config.warning_threshold:
            return TokenState.WARNING
        else:
            return TokenState.NORMAL

    def record_token_count(self, count: int) -> None:
        """Record a token count for history tracking."""
        self._token_history.append(count)

    def record_compact_success(self) -> None:
        """Reset failure counter on successful compaction."""
        self._compact_failure_count = 0

    def record_compact_failure(self) -> None:
        """Increment failure counter."""
        self._compact_failure_count += 1
        logger.warning(
            "Context compaction failed (consecutive failures: %d/%d)",
            self._compact_failure_count,
            self.config.max_compact_failures,
        )

    @property
    def circuit_breaker_tripped(self) -> bool:
        """True if too many consecutive compaction failures occurred."""
        return self._compact_failure_count >= self.config.max_compact_failures

    @property
    def token_history(self) -> List[int]:
        """Return recorded token count history."""
        return list(self._token_history)
