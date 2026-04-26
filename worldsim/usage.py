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
    "gpt-5.2": ModelPricing(1.75, 14.0, 0.175),
    "gpt-5.2-chat-latest": ModelPricing(1.75, 14.0, 0.175),
    "gpt-5.1": ModelPricing(1.25, 10.0, 0.125),
    "gpt-5.1-chat-latest": ModelPricing(1.25, 10.0, 0.125),
    "gpt-5": ModelPricing(1.25, 10.0, 0.125),
    "gpt-5-chat-latest": ModelPricing(1.25, 10.0, 0.125),
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
    if model in DEFAULT_PRICING:
        return DEFAULT_PRICING[model]
    for prefix, pricing in DEFAULT_PRICING.items():
        if model.startswith(f"{prefix}-"):
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
