from __future__ import annotations
import os
import time
from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Any, Dict, List

import anthropic
from transformers import AutoTokenizer

from .llm import BaseLLM, ChatMessage, LLMResponse

# ── Claude May-2025 pricing (USD / 1 000 000 tokens) ──────────────────────────
_PRICE = {
    # model                       (base_in, base_out)
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-3-7-sonnet-20250219": (3.0, 15.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.25, 1.25),
}

# cache multipliers
CACHE_WRITE_5M = 1.25  # 5-minute cache write
CACHE_READ = 0.10  # cache hit / refresh


@dataclass
class FakeContext:
    text: str
    type: str


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


@dataclass
class FakeClaudeResponse:
    content: List[FakeContext]
    model: str
    usage: FakeUsage

    def json(self):
        return asdict(self)


class ClaudeLLM(BaseLLM):
    """
    Claude Messages API wrapper with built-in 5-minute prompt caching
    and precise cost calculation.
    """

    def __init__(self, model: str = "claude-sonnet-4-20250514", temperature: float = 0.9, max_tokens: int = 2000, api_key: str = "ANTHROPIC_API_KEY"):
        super().__init__(model)
        self.temperature = temperature
        self.max_tokens = max_tokens

        self.client = anthropic.Anthropic(api_key=api_key)
        self.base_in, self.base_out = _PRICE.get(model, (0.0, 0.0))
        self.id = f"{self.__class__.__name__}_{self.model}"

    def simulate_completion_request(self, messages: list[dict], **extra) -> Any:
        cache_read_input_tokens = 0
        if len(messages) > 2:
            cached_history = messages[:-2]
            response = self.client.messages.count_tokens(
                model=self.model,
                messages=cached_history,
                **extra
            )
            cache_read_input_tokens = response.input_tokens
        cache_input = messages[-2:]
        response = self.client.messages.count_tokens(
            model=self.model,
            messages=cache_input,
            system=extra["system"] if len(messages) <= 2 else [],
        )
        cache_creation_input_tokens = response.input_tokens
        output_tokens = int(cache_creation_input_tokens * 0.9)  # About right for jp to en
        response = FakeClaudeResponse(
            content=[FakeContext(
                text="[Simulated response]",
                type="text"
            )],
            model=self.model,
            usage=FakeUsage(
                input_tokens=0,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens
            )
        )
        return response

    def run_completion_request(self, messages: list[dict], **extra) -> Any:
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=messages,
            **extra,
        )

    def chat(self, messages: list[ChatMessage], simulate: bool = False, **extra) -> LLMResponse:
        if not messages:
            raise ValueError("messages list must not be empty")

        # Build payload & tag LAST message for 5-minute cache
        api_msgs: list[dict] = []
        system = []
        for i, m in enumerate(messages):
            if m.role == "system":
                system = [{
                    "text": m.content,
                    "type": "text",
                    "cache_control": {"type": "ephemeral"},
                }]
                continue
            block = {"role": m.role, "content": [{"type": "text", "text": m.content}]}
            if i == len(messages) - 1:
                block["content"][0]["cache_control"] = {"type": "ephemeral"}  # 5 m default
            api_msgs.append(block)

        run_func = self.simulate_completion_request if simulate else self.run_completion_request
        t0 = time.perf_counter()
        try:
            resp = run_func(messages=api_msgs, system=system, **extra)
        except anthropic.RateLimitError as e:
            raise RuntimeError("Claude API rate limit exceeded") from e
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"Claude API returned HTTP error {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise RuntimeError("Claude API connection failed") from e
        except Exception as e:
            raise RuntimeError("Unexpected Claude API error") from e

        latency = round(time.perf_counter() - t0, 3)

        # Combine assistant blocks into a single string
        assistant_text = "".join(b.text for b in resp.content).strip()

        # ---- usage fields ------------------------------------------------
        u = resp.usage
        reg_in = u.input_tokens or 0
        cache_wr = getattr(u, "cache_creation_input_tokens", 0)
        cache_rd = getattr(u, "cache_read_input_tokens", 0)
        out_tok = u.output_tokens or 0

        # ---- cost -------------------------------------------------------
        cost = self._calculate_costs(reg_in, cache_wr, cache_rd, out_tok)

        return LLMResponse(
            content=assistant_text,
            tokens_in=reg_in + cache_wr + cache_rd,
            tokens_out=out_tok,
            model=resp.model,
            latency=latency,
            cost=cost,
            provider_raw=resp.json(),
        )

    def _calculate_costs(self, reg_in, cache_wr, cache_rd, out_tok):
        cost = (
                reg_in / 1000000 * self.base_in +
                cache_wr / 1000000 * self.base_in * CACHE_WRITE_5M +
                cache_rd / 1000000 * self.base_in * CACHE_READ +
                out_tok / 1000000 * self.base_out
        )

        return round(cost, 6)
