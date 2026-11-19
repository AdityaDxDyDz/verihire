"""E7: LLM client (Claude via the Anthropic SDK, JSON via forced tool use) + StubLLM.

Works WITHOUT a key: get_llm_client() returns StubLLM (rule-based + optional recorded replies).
Model ids come from env (LLM_MODEL_FAST / LLM_MODEL_STRONG): check the current names in the
Anthropic docs before setting them. Nothing here hard-codes a model name.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("docint.llm")
RECORDED_DIR = Path(__file__).resolve().parents[2] / "tests" / "llm" / "recorded"


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    model: str

    def complete_json(self, *, system: str, user: str, tool_name: str, schema: dict,
                      payload: dict | None = None, tier: str = "fast") -> dict: ...


def request_key(tool_name: str, user: str) -> str:
    return hashlib.sha256((tool_name + "\n" + user).encode("utf-8")).hexdigest()[:16]


class AnthropicLLM:
    def __init__(self, api_key: str, models: dict[str, str], timeout: float = 60.0, max_retries: int = 3,
                 recorder_dir: Path | None = None):
        import anthropic  # imported lazily so the package works without the SDK
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.models = models
        self.model = models["fast"]
        self.max_retries = max_retries
        self.recorder_dir = recorder_dir
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def complete_json(self, *, system: str, user: str, tool_name: str, schema: dict,
                      payload: dict | None = None, tier: str = "fast") -> dict:
        model = self.models.get(tier, self.model)
        self.model = model
        delay = 1.0
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.messages.create(
                    model=model, max_tokens=2048, system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[{"name": tool_name, "description": "Return the structured result.", "input_schema": schema}],
                    tool_choice={"type": "tool", "name": tool_name})
                self.total_input_tokens += resp.usage.input_tokens
                self.total_output_tokens += resp.usage.output_tokens
                log.info("llm call tool=%s model=%s in=%d out=%d attempt=%d", tool_name, model,
                         resp.usage.input_tokens, resp.usage.output_tokens, attempt)
                for block in resp.content:
                    if block.type == "tool_use" and block.name == tool_name:
                        data = dict(block.input)
                        if self.recorder_dir:  # save real responses for offline replay tests
                            self.recorder_dir.mkdir(parents=True, exist_ok=True)
                            (self.recorder_dir / f"{request_key(tool_name, user)}.json").write_text(
                                json.dumps({"tool": tool_name, "response": data}, indent=2), encoding="utf-8")
                        return data
                raise LLMError("no tool_use block in response")
            except (self._anthropic.RateLimitError, self._anthropic.APIConnectionError,
                    self._anthropic.InternalServerError, LLMError) as e:
                last = e
                log.warning("llm retry %d/%d after %s", attempt, self.max_retries, type(e).__name__)
                time.sleep(delay)
                delay *= 2
        raise LLMError(f"LLM failed after {self.max_retries} attempts: {last}")


class StubLLM:
    """Offline stand-in. Replays recorded real responses when one matches the request, else
    answers with deterministic rule-based logic. Never needs a key or network."""
    model = "stub-llm"

    def __init__(self, recorded_dir: Path | None = RECORDED_DIR):
        self.recorded_dir = recorded_dir

    def complete_json(self, *, system: str, user: str, tool_name: str, schema: dict,
                      payload: dict | None = None, tier: str = "fast") -> dict:
        if self.recorded_dir:
            f = self.recorded_dir / f"{request_key(tool_name, user)}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))["response"]
        payload = payload or {}
        if tool_name == "emit_job_spec":
            from docint.llm.jd_rules import rule_based_job_spec
            return rule_based_job_spec(payload.get("text", ""))
        if tool_name == "emit_skill_gap":
            from docint.llm.skillgap_rules import rule_based_skill_gap
            return rule_based_skill_gap(payload)
        raise LLMError(f"StubLLM cannot handle tool {tool_name}")


def get_llm_client(recorder_dir: Path | None = None) -> Any:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    fast, strong = os.environ.get("LLM_MODEL_FAST", "").strip(), os.environ.get("LLM_MODEL_STRONG", "").strip()
    if key and (fast or strong):
        try:
            return AnthropicLLM(key, {"fast": fast or strong, "strong": strong or fast}, recorder_dir=recorder_dir)
        except ImportError:
            log.warning("anthropic SDK missing: using StubLLM")
    return StubLLM()
