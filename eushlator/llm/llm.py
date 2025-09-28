from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Any, Union, Dict


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    tokens_in: int
    tokens_out: int
    model: str
    latency: float  # seconds
    cost: float     # USD
    provider_raw: Any


class BaseLLM(ABC):
    def __init__(self, model):
        self.model = model
        self.id = "Undefined"

    @abstractmethod
    def chat(
        self,
        messages: List[Union[ChatMessage, Dict]],
        simulate: bool = False,
        **extra
    ) -> Union[LLMResponse, Dict[str, LLMResponse]]:
        """
        Process the full conversation history and return an LLMResponse.
        Subclasses must implement how this is executed.
        """
        raise NotImplementedError

    @abstractmethod
    def run_completion_request(self, messages: List[ChatMessage], **extra) -> Any:
        """
        Subclasses must isolate the raw API call here (e.g. self.client.messages.create(...)).
        This allows `.simulate()` to bypass actual API calls.
        """
        raise NotImplementedError

    @abstractmethod
    def simulate_completion_request(self, messages: List[ChatMessage], **extra) -> Any:
        """
        Simulates an API call: tokenizes the prompt and pretends output is 1.8×
        the last user message's token count.
        """
        raise NotImplementedError
