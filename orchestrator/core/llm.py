"""LLM backends.

ClaudeLLM  - live calls to the Anthropic Messages API (the POC's engine).
ReplayLLM  - replays a recorded run (responses captured earlier) so a client
             demo can be re-run deterministically without network access.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LLMResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    latency_s: float = 0.0


class ClaudeLLM:
    """Live Claude backend. Requires ANTHROPIC_API_KEY."""

    def __init__(self, model: str | None = None, max_tokens: int = 8000):
        try:
            import anthropic  # noqa: WPS433
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("pip install anthropic") from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set. Export it to run the POC live.")
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("POC_MODEL", "claude-sonnet-4-5")
        self.max_tokens = max_tokens

    def complete(self, agent: str, system: str, user: str) -> LLMResult:
        start = time.time()
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        return LLMResult(
            text=text,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            model=self.model,
            latency_s=round(time.time() - start, 2),
        )


@dataclass
class ReplayLLM:
    """Replays responses recorded per agent, in call order."""

    path: Path
    queues: dict[str, list[dict]] = field(default_factory=dict)

    def __post_init__(self):
        data = json.loads(Path(self.path).read_text(encoding="utf-8"))
        for item in data["responses"]:
            self.queues.setdefault(item["agent"], []).append(item)
        self.model = data.get("model", "recorded")

    def complete(self, agent: str, system: str, user: str) -> LLMResult:
        queue = self.queues.get(agent)
        if not queue:
            raise RuntimeError(f"No recorded response left for agent '{agent}'")
        item = queue.pop(0)
        return LLMResult(text=item["response"], model=self.model,
                         input_tokens=item.get("input_tokens"),
                         output_tokens=item.get("output_tokens"))


class RecordingLLM:
    """Wraps a live backend and records every response for later replay."""

    def __init__(self, inner):
        self.inner = inner
        self.model = getattr(inner, "model", None)
        self.responses: list[dict] = []

    def complete(self, agent: str, system: str, user: str) -> LLMResult:
        res = self.inner.complete(agent, system, user)
        self.responses.append({"agent": agent, "response": res.text,
                               "input_tokens": res.input_tokens,
                               "output_tokens": res.output_tokens})
        return res

    def save(self, path: Path):
        Path(path).write_text(json.dumps({"model": self.model, "responses": self.responses}, indent=2),
                              encoding="utf-8")
