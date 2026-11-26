"""Bring-your-own-agent adapters.

Every seat in the org chart names an adapter. The engine only needs `run(agent, system, prompt)`
to return text and usage, so any agent runtime that can take a prompt can be hired:

  claude_api   Anthropic Messages API, billed per token with an API key
  claude_code  Claude Code CLI in headless mode (`claude -p`). Signed in with a Claude Pro/Max/Team/Enterprise
               plan, this runs on your **subscription** instead of an API key. The agent may read the repository
               (Read/Glob/Grep) but cannot edit files or run commands; the orchestrator writes its output.
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


class AgentUnavailable(Exception):
    """The agent runtime can't work right now (not signed in, usage limit reached). The engine pauses the
    project instead of failing it; `resume` continues once the runtime is available again."""


@dataclass
class AdapterSpec:
    type: str = "claude_api"
    model: str | None = None
    url: str | None = None          # http
    timeout_s: int = 900
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
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AgentUnavailable("ANTHROPIC_API_KEY is not set. Set an API key, or run on your Claude "
                                   "subscription with --runtime claude-subscription.")
        self.llm = ClaudeLLM(model=spec.model)
        self.model = self.llm.model

    def run(self, agent_key: str, system: str, prompt: str, cwd: str | None = None) -> LLMResult:
        return self.llm.complete(agent_key, system, prompt)


READ_ONLY_TOOLS = "Read,Glob,Grep"


def subscription_env() -> dict:
    """Environment for Claude Code on a subscription: without ANTHROPIC_API_KEY, Claude Code uses the
    signed-in Claude account (OAuth) instead of per-token API billing."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    return env


def claude_cli() -> str | None:
    return os.environ.get("CLAUDE_CODE_BIN") or shutil.which("claude")


class ClaudeCodeAdapter:
    """Runs one agent turn through Claude Code (`claude -p`), on the user's Claude subscription."""

    def __init__(self, spec: AdapterSpec, subscription: bool = True):
        self.bin = claude_cli()
        if not self.bin:
            raise AgentUnavailable("Claude Code CLI ('claude') not found. Install it with "
                                   "`npm install -g @anthropic-ai/claude-code`, then run `claude` and /login.")
        self.spec = spec
        self.subscription = subscription
        self.model = spec.model or "claude-code"

    def run(self, agent_key: str, system: str, prompt: str, cwd: str | None = None) -> LLMResult:
        cmd = [self.bin, "-p", "--output-format", "json", "--append-system-prompt", system,
               "--tools", READ_ONLY_TOOLS, "--no-session-persistence"]
        if self.spec.model:
            cmd += ["--model", self.spec.model]
        cmd += self.spec.extra_args or []
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True,
                                  timeout=self.spec.timeout_s, env=subscription_env() if self.subscription else None)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Claude Code did not answer within {self.spec.timeout_s}s") from exc
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude Code returned unexpected output: {proc.stdout[:400]} {proc.stderr[:400]}") from exc
        if data.get("is_error"):
            message = str(data.get("result") or data.get("terminal_reason") or "unknown error")
            low = message.lower()
            if any(k in low for k in ("authentication", "not logged in", "/login", "invalid api key", "oauth")):
                raise AgentUnavailable(f"Claude Code is not signed in ({message}). Run `claude` in a terminal and "
                                       "use /login with your Claude subscription, then resume.")
            if data.get("api_error_status") == 429 or any(k in low for k in ("usage limit", "rate limit",
                                                                              "limit reached", "overloaded")):
                raise AgentUnavailable(f"Claude usage limit reached ({message}). Resume when your plan's limit resets.")
            raise RuntimeError(f"Claude Code error: {message}")
        usage = data.get("usage", {}) or {}
        tin = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) + \
              (usage.get("cache_creation_input_tokens") or 0)
        model = next(iter(data.get("modelUsage") or {}), None) or self.spec.model or "claude-code"
        return LLMResult(text=data.get("result", ""), model=model, latency_s=round(time.time() - t0, 2),
                         input_tokens=tin or None, output_tokens=usage.get("output_tokens"),
                         cost_usd=data.get("total_cost_usd"),
                         billing="subscription" if self.subscription else "api")


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


RUNTIMES = ("api", "claude-subscription")


def build_adapter(value, replay: ReplayLLM | None = None, runtime: str = "api"):
    """runtime="claude-subscription" runs every Claude seat through Claude Code on the user's plan."""
    if replay is not None:
        return ReplayAdapter(replay)
    spec = AdapterSpec.from_value(value)
    if runtime == "claude-subscription" and spec.type in ("claude_api", "claude_code"):
        return ClaudeCodeAdapter(spec, subscription=True)
    if spec.type == "claude_api":
        return ClaudeApiAdapter(spec)
    if spec.type == "claude_code":
        return ClaudeCodeAdapter(spec, subscription=not os.environ.get("ANTHROPIC_API_KEY"))
    if spec.type == "http":
        return HttpAdapter(spec)
    raise ValueError(f"Unknown adapter type: {spec.type}")
