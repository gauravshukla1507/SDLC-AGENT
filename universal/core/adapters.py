"""Bring-your-own-agent adapters.

Every seat in the org chart names an adapter. The engine only needs `run(agent, system, prompt)`
to return text and usage, so any agent runtime that can take a prompt can be hired:

  claude_api   Anthropic Messages API (default)
  claude_code  Claude Code CLI in headless mode (`claude -p`), working inside the workspace
  http         Any webhook/bot: POST {agent, system, prompt} -> {text, input_tokens, output_tokens}
  replay       Recorded responses, for offline demos and tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass

from .llm import ClaudeLLM, LLMResult, ReplayLLM


@dataclass
class AdapterSpec:
    type: str = "claude_api"
    model: str | None = None
    url: str | None = None          # http
    timeout_s: int = 600
    extra_args: list[str] | None = None

    @classmethod
    def from_value(cls, value) -> "AdapterSpec":
        if value is None:
            return cls()
        if isinstance(value, str):
            return cls(type=value)
        return cls(**value)


class ClaudeApiAdapter:
    def __init__(self, spec: AdapterSpec):
        self.llm = ClaudeLLM(model=spec.model)
        self.model = self.llm.model

    def run(self, agent_key: str, system: str, prompt: str, cwd: str | None = None) -> LLMResult:
        return self.llm.complete(agent_key, system, prompt)


class ClaudeCodeAdapter:
    """Runs Claude Code headless. The agent can read the repo itself; output contract is unchanged."""

    def __init__(self, spec: AdapterSpec):
        if not shutil.which("claude"):
            raise SystemExit("Claude Code CLI ('claude') not found on PATH for the claude_code adapter.")
        self.spec = spec
        self.model = spec.model or "claude-code"

    def run(self, agent_key: str, system: str, prompt: str, cwd: str | None = None) -> LLMResult:
        cmd = ["claude", "-p", prompt, "--append-system-prompt", system, "--output-format", "json"]
        if self.spec.model:
            cmd += ["--model", self.spec.model]
        cmd += self.spec.extra_args or []
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=self.spec.timeout_s)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude_code adapter: unexpected output: {proc.stdout[:500]} {proc.stderr[:500]}") from exc
        usage = data.get("usage", {}) or {}
        return LLMResult(text=data.get("result", ""), model=self.model, latency_s=round(time.time() - t0, 2),
                         input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))


class HttpAdapter:
    def __init__(self, spec: AdapterSpec):
        if not spec.url:
            raise ValueError("http adapter needs 'url'")
        self.spec = spec
        self.model = spec.model or "http"

    def run(self, agent_key: str, system: str, prompt: str, cwd: str | None = None) -> LLMResult:
        body = json.dumps({"agent": agent_key, "system": system, "prompt": prompt}).encode()
        req = urllib.request.Request(self.spec.url, data=body, headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {os.environ['UO_HTTP_AGENT_TOKEN']}"}
               if os.environ.get("UO_HTTP_AGENT_TOKEN") else {})})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.spec.timeout_s) as resp:  # noqa: S310 (operator-configured URL)
            data = json.loads(resp.read().decode())
        return LLMResult(text=data["text"], model=self.model, latency_s=round(time.time() - t0, 2),
                         input_tokens=data.get("input_tokens"), output_tokens=data.get("output_tokens"))


class ReplayAdapter:
    def __init__(self, replay: ReplayLLM):
        self.replay = replay
        self.model = "recorded"

    def run(self, agent_key: str, system: str, prompt: str, cwd: str | None = None) -> LLMResult:
        return self.replay.complete(agent_key, system, prompt)


def build_adapter(value, replay: ReplayLLM | None = None):
    if replay is not None:
        return ReplayAdapter(replay)
    spec = AdapterSpec.from_value(value)
    if spec.type == "claude_api":
        return ClaudeApiAdapter(spec)
    if spec.type == "claude_code":
        return ClaudeCodeAdapter(spec)
    if spec.type == "http":
        return HttpAdapter(spec)
    raise ValueError(f"Unknown adapter type: {spec.type}")
