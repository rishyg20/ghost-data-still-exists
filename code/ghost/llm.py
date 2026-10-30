"""Claude client wrapper: structured JSON calls with caching, validation, fallback and usage tracking.

Every call:
  1. builds a cache key from (model, prompt version, system prompt, content fingerprint, schema);
  2. returns the cached reply if present (re-runs are free and identical);
  3. otherwise calls the Messages API with a JSON-schema output format;
  4. validates the reply against the schema (one retry on failure);
  5. records tokens and cost per model, and saves the full prompt + reply for inspection.
Any failure (no key, network, refusal, invalid JSON) returns None so callers fall back to rules.
"""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from .data import REPO_ROOT

PROMPT_VERSION = "v1"
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
# USD per 1M tokens (input, output), Anthropic first-party pricing for the models above.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}
CACHE_DIR = REPO_ROOT / "code" / "cache" / "llm"


def load_env(path=REPO_ROOT / ".env"):
    """Read KEY=VALUE lines from an uncommitted .env into os.environ (never printed or logged)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def validate(value, schema, path="$"):
    """Minimal JSON-schema check (type, enum, required, properties, items). Returns list of errors."""
    errs = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}
    if t in types and not isinstance(value, types[t]):
        return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}.{k}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += validate(value[k], sub, f"{path}.{k}")
    if t == "array":
        for i, item in enumerate(value):
            errs += validate(item, schema.get("items", {}), f"{path}[{i}]")
    return errs


def image_block(path):
    data = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def pdf_block(path):
    data = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


class LLM:
    def __init__(self, offline=False):
        load_env()
        self.usage = []          # one record per call (live or cached)
        self.failures = []       # (purpose, reason)
        self.client = None
        self.enabled = not offline and bool(os.environ.get("ANTHROPIC_API_KEY"))
        if self.enabled:
            import anthropic
            self._anthropic = anthropic
            self.client = anthropic.Anthropic(max_retries=3, timeout=120.0)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public
    def structured(self, purpose, model, system, blocks, schema, fingerprint, effort="low", max_tokens=4000):
        """Return a dict that validates against `schema`, or None (caller falls back).

        blocks: message content blocks (text / image / document).
        fingerprint: stable text identifying the content (e.g. file sha256), used in the cache key.
        """
        key = hashlib.sha256(json.dumps([model, PROMPT_VERSION, system, fingerprint, schema],
                                        sort_keys=True).encode()).hexdigest()[:24]
        cache_file = CACHE_DIR / f"{purpose}_{key}.json"
        if cache_file.exists():
            rec = json.loads(cache_file.read_text(encoding="utf-8"))
            self._record(purpose, model, rec["usage"], cached=True)
            return rec["parsed"]
        if not self.enabled:
            self.failures.append((purpose, "LLM disabled (offline or no ANTHROPIC_API_KEY)"))
            return None
        for attempt in (1, 2):
            reply, usage, reason = self._call(model, system, blocks, schema, effort, max_tokens)
            if usage:
                self._record(purpose, model, usage, cached=False)
            if reply is None:
                self.failures.append((purpose, reason))
                if reason.startswith("fatal"):
                    return None
                continue
            errs = validate(reply, schema)
            if not errs:
                cache_file.write_text(json.dumps({
                    "purpose": purpose, "model": model, "prompt_version": PROMPT_VERSION,
                    "system": system, "content": [b if b["type"] == "text" else {"type": b["type"], "source": "<file>"}
                                                  for b in blocks],
                    "fingerprint": fingerprint, "parsed": reply, "usage": usage,
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }, indent=2, ensure_ascii=False), encoding="utf-8")
                return reply
            self.failures.append((purpose, f"schema invalid (attempt {attempt}): {errs[:3]}"))
        return None

    def summary(self):
        """Per-model totals for usage_report.md."""
        out = {}
        for r in self.usage:
            m = out.setdefault(r["model"], {"live_calls": 0, "cached_calls": 0, "input_tokens": 0,
                                            "output_tokens": 0, "live_input_tokens": 0, "live_output_tokens": 0,
                                            "by_purpose": {}})
            m["cached_calls" if r["cached"] else "live_calls"] += 1
            m["input_tokens"] += r["input_tokens"]
            m["output_tokens"] += r["output_tokens"]
            if not r["cached"]:
                m["live_input_tokens"] += r["input_tokens"]
                m["live_output_tokens"] += r["output_tokens"]
            m["by_purpose"][r["purpose"]] = m["by_purpose"].get(r["purpose"], 0) + 1
        for model, m in out.items():
            pin, pout = PRICES.get(model, (0, 0))
            m["cost_usd"] = (m["input_tokens"] * pin + m["output_tokens"] * pout) / 1e6
            m["live_cost_usd"] = (m["live_input_tokens"] * pin + m["live_output_tokens"] * pout) / 1e6
        return out

    # ------------------------------------------------------------------ internals
    def _record(self, purpose, model, usage, cached):
        self.usage.append({"purpose": purpose, "model": model, "cached": cached,
                           "input_tokens": usage.get("input_tokens", 0),
                           "output_tokens": usage.get("output_tokens", 0)})

    def _call(self, model, system, blocks, schema, effort, max_tokens):
        a = self._anthropic
        try:
            resp = self.client.messages.create(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": blocks}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except (a.AuthenticationError, a.PermissionDeniedError) as e:
            self.enabled = False  # bad credentials: stop calling, everything falls back to rules
            return None, None, f"fatal auth error: {type(e).__name__}"
        except a.BadRequestError as e:
            return None, None, f"fatal bad request: {str(e)[:200]}"
        except (a.RateLimitError, a.APIConnectionError, a.InternalServerError, a.APIStatusError) as e:
            return None, None, f"transient: {type(e).__name__}"
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        if resp.stop_reason in ("refusal", "max_tokens"):
            return None, usage, f"stop_reason={resp.stop_reason}"
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text), usage, ""
        except ValueError:
            return None, usage, "reply was not valid JSON"
