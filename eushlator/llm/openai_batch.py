from __future__ import annotations

from openai.types import Batch

"""openai_batch_llm.py – Batch wrapper compatible with OpenAI’s Batch API
(and a drop‑in replacement for the old ``ClaudeBatchLLM``).

Key points
~~~~~~~~~~
*   Supports **/v1/chat/completions** batch jobs.
*   Handles automatic prompt‑cache discounts (≈ 50 % of base input rate).
*   Preserves original public method names & return signatures.
*   Fully aligns with the official Batch API docs (May‑2025).
"""

import json
import os
import tempfile
import time
from typing import Any, Dict, List

from openai import OpenAI
from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError

from .llm import BaseLLM, LLMResponse

# ── OpenAI May‑2025 batch pricing (USD / 1000000 tokens) ────────────────
# Extend as necessary for your organisation’s rate‑card.
_PRICE: dict[str, tuple[float, float, float]] = {
    # model         (input, cached input, output)
    "gpt-4o":       (2.50, 1.25, 10.00),
    "gpt-4o-mini":  (0.15, 0.075, 0.60),
    "gpt-4.1":      (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
}


class OpenAIBatchLLM(BaseLLM):
    """Batch‑mode wrapper around OpenAI Chat completions."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.9,
        max_tokens: int = 500,
        api_key: str = "OPENAI_API_KEY",
    ) -> None:
        super().__init__(model)
        self.temperature = temperature
        self.max_tokens = max_tokens

        self.client = OpenAI(api_key=api_key)
        self.base_in, self.base_cached_in, self.base_out = _PRICE.get(model, (0.0, 0.0, 0.0))
        self.id = f"{self.__class__.__name__}_{self.model}"

    # ------------------------------------------------------------------
    # JSONL helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _combine_system(system_prompt: str, system_context: str) -> str:
        return f"{system_prompt}\n\n{system_context}".strip()

    def create_batch_request(
        self,
        msg_id: str,
        system_prompt: str,
        system_context: str,
        content: str,
    ) -> Dict[str, Any]:
        """Return a single JSONL‑line payload for the Batch file."""
        return {
            "custom_id": msg_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "messages": [
                    {"role": "system", "content": self._combine_system(system_prompt, system_context)},
                    {"role": "user", "content": content},
                ],
            },
        }

    # ------------------------------------------------------------------
    # API orchestration
    # ------------------------------------------------------------------
    def simulate_completion_request(self, messages: list[dict], **extra) -> Any:  # noqa: D401,E501 – kept for interface parity
        raise NotImplementedError("Simulation mode not implemented for Batch API.")

    def run_completion_request(self, messages: list[dict], **extra) -> Batch:
        """Upload JSONL file and kick off the batch job."""
        system_prompt = extra["system"]
        requests = [
            self.create_batch_request(r["msg_id"], system_prompt, r["add_context"], r["message"])  # type: ignore[index]
            for r in messages
        ]

        # 1️⃣  Write temp .jsonl file
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fp:
            for req in requests:
                fp.write(json.dumps(req, ensure_ascii=False) + "\n")
            jsonl_path = fp.name

        # 2️⃣  Upload file
        with open(jsonl_path, "rb") as fh:
            file_obj = self.client.files.create(file=fh, purpose="batch")

        # 3️⃣  Kick off batch
        return self.client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def get_batch_status(self, batch_id: str) -> str:
        meta: Batch = self.client.batches.retrieve(batch_id)
        counts = meta.request_counts or {}
        done = counts.completed or 0
        total = counts.total or 0
        print(f"Batch: {meta.status}, {done}/{total} parts succeeded")
        return str(meta.status)

    def get_batch_results(self, batch_id: str) -> List[dict]:
        meta = self.client.batches.retrieve(batch_id)
        if meta.status != "completed":
            raise RuntimeError("Batch is not completed yet.")

        if not meta.output_file_id:
            raise RuntimeError("Batch completed without output_file_id.")

        file_resp = self.client.files.content(meta.output_file_id)
        data = file_resp.text  # returns str
        return [json.loads(line) for line in data.splitlines() if line]

    # ------------------------------------------------------------------
    # Public entry‑point
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict],
        simulate: bool = False,
        **extra,
    ) -> Dict[str, LLMResponse]:
        if not messages:
            raise ValueError("messages list must not be empty")

        runner = self.simulate_completion_request if simulate else self.run_completion_request

        t0 = time.perf_counter()
        try:
            batch_job = runner(messages=messages, **extra)
        except RateLimitError as exc:  # noqa: PERF203
            raise RuntimeError("OpenAI API rate limit exceeded") from exc
        except APIStatusError as exc:
            raise RuntimeError(f"OpenAI API HTTP {exc.status_code}") from exc
        except APIConnectionError as exc:
            raise RuntimeError("Failed to connect to OpenAI API") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Unexpected OpenAI API error") from exc

        batch_id = batch_job.id

        # Poll until finished (≤24h window).
        tries = 0
        while True:
            state = self.get_batch_status(batch_id)
            if state == "completed":
                results = self.get_batch_results(batch_id)
                break
            if state in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"Batch ended with terminal state: {state}")
            if tries > 1_000:  # ~2h @ 7.2min; adjust if needed
                raise TimeoutError("Batch polling exceeded expected window.")
            tries += 1
            time.sleep(10)

        latency = round(time.perf_counter() - t0, 3)

        # ------------------------------------------------------------------
        # Assemble per‑request results
        # ------------------------------------------------------------------
        responses: Dict[str, LLMResponse] = {}
        for line in results:
            custom_id = str(line.get("custom_id"))

            # Handle errors (line["error"] may contain details)
            if line.get("error"):
                err_info = line["error"]
                responses[custom_id] = LLMResponse(
                    content="",
                    tokens_in=0,
                    tokens_out=0,
                    model=self.model,
                    latency=latency,
                    cost=0.0,
                    provider_raw=err_info,
                )
                continue

            resp_obj = line["response"]["body"]  # as per docs – chat completion object
            assistant_text = resp_obj["choices"][0]["message"]["content"].strip()

            usage = resp_obj.get("usage", {})
            prompt_total = usage.get("prompt_tokens", 0)
            cached_prompt = usage.get("cached_tokens") or (
                usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
            )
            cached_prompt = cached_prompt or 0
            regular_prompt = max(prompt_total - cached_prompt, 0)
            completion_tokens = usage.get("completion_tokens", 0)

            cost = self._calculate_costs(regular_prompt, 0, cached_prompt, completion_tokens)

            responses[custom_id] = LLMResponse(
                content=assistant_text,
                tokens_in=prompt_total,
                tokens_out=completion_tokens,
                model=resp_obj.get("model", self.model),
                latency=latency,
                cost=cost,
                provider_raw=resp_obj,
            )

        return responses

    # ------------------------------------------------------------------
    # Pricing helper
    # ------------------------------------------------------------------
    def _calculate_costs(
        self,
        reg_in: int,
        cache_wr: int,  # OpenAI doesn’t expose write‑cache tokens, pass 0.
        cache_rd: int,
        out_tok: int,
    ) -> float:
        cost = (
            reg_in / 1_000_000 * self.base_in
            + cache_rd / 1_000_000 * self.base_cached_in
            + out_tok / 1_000_000 * self.base_out
        )
        return round(cost, 6)
