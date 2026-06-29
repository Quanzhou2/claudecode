"""Fake OpenAI-compatible clients used to test LLM code paths offline."""
from __future__ import annotations

import json
from types import SimpleNamespace


def _response(content: str):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeVisionClient:
    """Returns a fixed completion regardless of input (for extraction tests)."""

    def __init__(self, content: str):
        self._content = content
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        return _response(self._content)


class FakeAnalysisClient:
    """Returns SQL for generation calls and text for summarization calls."""

    def __init__(self, sql: str, summary: str = "Looks reasonable."):
        self.sql = sql
        self.summary = summary
        self.chat = self
        self.completions = self
        self.calls: list[str] = []

    def create(self, model=None, messages=None, **kwargs):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        if "read-only SQLite SELECT" in prompt:
            return _response(json.dumps({"sql": self.sql, "explanation": "demo"}))
        return _response(self.summary)
