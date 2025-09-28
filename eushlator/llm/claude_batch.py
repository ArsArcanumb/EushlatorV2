from __future__ import annotations
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Union

import anthropic
from anthropic.types.messages.batch_create_params import Request
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from .llm import BaseLLM, ChatMessage, LLMResponse

# ── Claude May-2025 batch pricing (USD / 1 000 000 tokens) ──────────────────────────
_PRICE = {
    # model                       (base_in, base_out)
    "claude-opus-4-20250514": (7.5, 37.5),
    "claude-3-7-sonnet-20250219": (1.5, 7.5),
    "claude-sonnet-4-20250514": (1.5, 7.5),
    "claude-3-5-haiku-20241022": (0.4, 2.0),
}

# cache multipliers
CACHE_WRITE_5M = 1.25  # 5-minute cache write
CACHE_READ = 0.10  # cache hit / refresh


class ClaudeBatchLLM(BaseLLM):
    """
    Claude Messages API wrapper with built-in 5-minute prompt caching
    and precise cost calculation.
    """

    def __init__(self, model: str = "claude-sonnet-4-20250514", temperature: float = 0.9, max_tokens: int = 1000, api_key: str = "ANTHROPIC_API_KEY"):
        super().__init__(model)
        self.temperature = temperature
        self.max_tokens = max_tokens

        self.client = anthropic.Anthropic(api_key=api_key)
        self.base_in, self.base_out = _PRICE.get(model, (0.0, 0.0))
        self.id = f"{self.__class__.__name__}_{self.model}"

    def create_batch_request(self, msg_id: str, system_prompt: str, system_context: str, content: str) -> Request:
        r = Request(
            custom_id=msg_id,
            params=MessageCreateParamsNonStreaming(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    },
                    {
                        "type": "text",
                        "text": system_context
                    }
                ],
                messages=[{
                    "role": "user",
                    "content": content,
                }]
            )
        )
        return r

    def simulate_completion_request(self, messages: list[dict], **extra) -> Any:
        raise NotImplementedError

    def run_completion_request(self, messages: list[dict], **extra) -> Any:
        """
        messages has format:
        [
            {
                msg_id: str
                add_context: str
                message: str
            },
            ...
        ]
        """
        system_prompt = extra["system"]
        requests = [self.create_batch_request(r["msg_id"], system_prompt, r["add_context"], r["message"]) for r in messages]
        return self.client.messages.batches.create(
            requests=requests,
        )

    def get_batch_status(self, batch_id):
        batch_status = self.client.messages.batches.retrieve(
            batch_id,
        )
        progress = batch_status.processing_status
        processing = batch_status.request_counts.processing
        succeeded = batch_status.request_counts.succeeded

        print(f"Batch: {progress}, {succeeded}/{processing} parts succeeded")

        return progress

    def get_batch_results(self, batch_id):
        results = []
        for result in self.client.messages.batches.results(
            batch_id,
        ):
            results.append(result)

        return results

    def chat(self, messages: List[Dict], simulate: bool = False, **extra) -> Dict[str, LLMResponse]:
        if not messages:
            raise ValueError("messages list must not be empty")

        run_func = self.simulate_completion_request if simulate else self.run_completion_request
        t0 = time.perf_counter()
        try:
            resp = run_func(messages=messages, **extra)
        except anthropic.RateLimitError as e:
            raise RuntimeError("Claude API rate limit exceeded") from e
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"Claude API returned HTTP error {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise RuntimeError("Claude API connection failed") from e
        except Exception as e:
            raise RuntimeError("Unexpected Claude API error") from e

        batch_id = resp.id

        wait_counter = 0
        while True:
            progress = self.get_batch_status(batch_id)
            if progress == "ended":
                results = self.get_batch_results(batch_id)
                break
            wait_counter += 1
            if wait_counter > 1000:
                raise Exception("Took too long")
            time.sleep(10)

        latency = round(time.perf_counter() - t0, 3)

        responses = {}
        for resp in results:
            # Combine assistant blocks into a single string
            resp_result = resp.result.message
            assistant_text = "".join(b.text for b in resp_result.content).strip()

            # ---- usage fields ------------------------------------------------
            u = resp_result.usage
            reg_in = u.input_tokens or 0
            cache_wr = getattr(u, "cache_creation_input_tokens", 0)
            cache_rd = getattr(u, "cache_read_input_tokens", 0)
            out_tok = u.output_tokens or 0

            # ---- cost -------------------------------------------------------
            cost = self._calculate_costs(reg_in, cache_wr, cache_rd, out_tok)

            llm_resp = LLMResponse(
                content=assistant_text,
                tokens_in=reg_in + cache_wr + cache_rd,
                tokens_out=out_tok,
                model=resp_result.model,
                latency=latency,
                cost=cost,
                provider_raw=resp_result.json(),
            )

            responses[resp.custom_id] = llm_resp

        return responses

    def _calculate_costs(self, reg_in, cache_wr, cache_rd, out_tok):
        cost = (
                reg_in / 1000000 * self.base_in +
                cache_wr / 1000000 * self.base_in * CACHE_WRITE_5M +
                cache_rd / 1000000 * self.base_in * CACHE_READ +
                out_tok / 1000000 * self.base_out
        )

        return round(cost, 6)
