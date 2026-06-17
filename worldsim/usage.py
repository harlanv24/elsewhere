from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float | None = None


DEFAULT_PRICING: dict[str, ModelPricing] = {
    "gpt-5.5": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5.5-pro": ModelPricing(30.0, 180.0),
    "gpt-5.4": ModelPricing(2.5, 15.0, 0.25),
    "gpt-5.4-mini": ModelPricing(0.75, 4.5, 0.075),
    "gpt-5.4-nano": ModelPricing(0.2, 1.25, 0.02),
    "gpt-5.4-pro": ModelPricing(30.0, 180.0),
    "gpt-5.3-codex": ModelPricing(1.75, 14.0, 0.175),
    "chatgpt-chat-latest": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5.4-chat-latest": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5.3-chat-latest": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5.2": ModelPricing(1.75, 14.0, 0.175),
    "gpt-5.2-chat-latest": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5.1": ModelPricing(1.25, 10.0, 0.125),
    "gpt-5.1-chat-latest": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5": ModelPricing(1.25, 10.0, 0.125),
    "gpt-5-chat-latest": ModelPricing(5.0, 30.0, 0.5),
    "gpt-5-mini": ModelPricing(0.25, 2.0, 0.025),
    "gpt-5-nano": ModelPricing(0.05, 0.4, 0.005),
    "gpt-4.1": ModelPricing(2.0, 8.0, 0.5),
    "gpt-4.1-mini": ModelPricing(0.4, 1.6, 0.1),
    "gpt-4.1-nano": ModelPricing(0.1, 0.4, 0.025),
    "gpt-4o": ModelPricing(2.5, 10.0, 1.25),
    "gpt-4o-mini": ModelPricing(0.15, 0.6, 0.075),
}


@dataclass
class UsageRecord:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    estimated_cost: float | None = None


@dataclass
class UsageTotals:
    request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    estimated_cost: float = 0.0

    def add_record(self, record: UsageRecord | None) -> None:
        if record is None:
            return
        self.request_count += 1
        self.prompt_tokens += record.prompt_tokens
        self.completion_tokens += record.completion_tokens
        self.total_tokens += record.total_tokens
        self.cached_prompt_tokens += record.cached_prompt_tokens
        if record.estimated_cost is not None:
            self.estimated_cost += record.estimated_cost

    def summary_line(self, label: str = "Usage") -> str:
        if self.request_count == 0:
            return f"{label}: no token data yet"
        cost = f"${self.estimated_cost:.4f}" if self.estimated_cost else "unknown cost"
        return (
            f"{label}: {self.request_count} req  |  "
            f"{format_tokens(self.total_tokens)} tokens  |  "
            f"{format_tokens(self.prompt_tokens)} in / {format_tokens(self.completion_tokens)} out  |  "
            f"est. {cost}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_count": self.request_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "estimated_cost": self.estimated_cost,
        }

    @classmethod
    def from_dict(cls, payload: object) -> UsageTotals:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            request_count=_safe_int(payload.get("request_count")),
            prompt_tokens=_safe_int(payload.get("prompt_tokens")),
            completion_tokens=_safe_int(payload.get("completion_tokens")),
            total_tokens=_safe_int(payload.get("total_tokens")),
            cached_prompt_tokens=_safe_int(payload.get("cached_prompt_tokens")),
            estimated_cost=_safe_float(payload.get("estimated_cost")),
        )


class TokenUsageTracker:
    def __init__(self) -> None:
        self.request_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_prompt_tokens = 0
        self.estimated_cost = 0.0
        self.last_record: UsageRecord | None = None

    def record(self, model: str, usage: dict[str, Any] | None) -> UsageRecord | None:
        if not usage:
            return None
        prompt_tokens = _safe_int(usage.get("prompt_tokens"))
        completion_tokens = _safe_int(usage.get("completion_tokens"))
        total_tokens = _safe_int(usage.get("total_tokens")) or prompt_tokens + completion_tokens
        details = usage.get("prompt_tokens_details")
        cached_tokens = _safe_int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        cost = estimate_cost(model, prompt_tokens, completion_tokens, cached_tokens)
        record = UsageRecord(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_prompt_tokens=cached_tokens,
            estimated_cost=cost,
        )
        self.request_count += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.cached_prompt_tokens += cached_tokens
        if cost is not None:
            self.estimated_cost += cost
        self.last_record = record
        return record

    def summary_line(self) -> str:
        if self.request_count == 0:
            return "LLM usage: no token data yet"
        cost = f"${self.estimated_cost:.4f}" if self.estimated_cost else "unknown cost"
        return (
            f"LLM usage: {self.request_count} req  |  "
            f"{format_tokens(self.total_tokens)} tokens  |  "
            f"{format_tokens(self.prompt_tokens)} in / {format_tokens(self.completion_tokens)} out  |  "
            f"est. {cost}"
        )

    def snapshot(self) -> UsageTotals:
        return UsageTotals(
            request_count=self.request_count,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens,
            estimated_cost=self.estimated_cost,
        )


def pricing_for_model(model: str) -> ModelPricing | None:
    env_input = os.getenv("WORLDSIM_LLM_INPUT_COST_PER_1M")
    env_output = os.getenv("WORLDSIM_LLM_OUTPUT_COST_PER_1M")
    if env_input is not None and env_output is not None:
        cached = os.getenv("WORLDSIM_LLM_CACHED_INPUT_COST_PER_1M")
        return ModelPricing(
            input_per_1m=float(env_input),
            output_per_1m=float(env_output),
            cached_input_per_1m=float(cached) if cached is not None else None,
        )
    normalized = model.lower()
    if normalized in DEFAULT_PRICING:
        return DEFAULT_PRICING[normalized]
    for prefix, pricing in sorted(DEFAULT_PRICING.items(), key=lambda item: len(item[0]), reverse=True):
        if normalized.startswith(f"{prefix}-"):
            return pricing
    return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int, cached_prompt_tokens: int = 0) -> float | None:
    pricing = pricing_for_model(model)
    if pricing is None:
        return None
    cached_prompt_tokens = min(cached_prompt_tokens, prompt_tokens)
    uncached_prompt_tokens = prompt_tokens - cached_prompt_tokens
    cached_rate = pricing.cached_input_per_1m if pricing.cached_input_per_1m is not None else pricing.input_per_1m
    return (
        (uncached_prompt_tokens / 1_000_000) * pricing.input_per_1m
        + (cached_prompt_tokens / 1_000_000) * cached_rate
        + (completion_tokens / 1_000_000) * pricing.output_per_1m
    )


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 10_000:
        return f"{count / 1_000:.1f}k"
    return f"{count:,}"


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0
