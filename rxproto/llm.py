"""Gemini calls with an on-disk cache, a simple rate limit, retries, usage counting and a call cap.

RX_FAKE=1 swaps in a fake reader so the pipeline can be tested without a key.
"""
import hashlib
import json
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
load_dotenv(ROOT / ".env")


class CallLimitReached(RuntimeError):
    pass


class Usage:
    def __init__(self):
        self.calls = 0
        self.cached = 0
        self.tokens = {}  # model -> [in, out, thinking]
        self.seconds = 0.0

    def add(self, model, tin, tout, tthink, secs):
        t = self.tokens.setdefault(model, [0, 0, 0])
        t[0] += tin or 0
        t[1] += tout or 0
        t[2] += tthink or 0
        self.seconds += secs

    def dollars(self):
        """Only if prices are set in .env (USD per 1M tokens). Thinking tokens are billed as output."""
        total, known = 0.0, True
        for model, (tin, tout, tth) in self.tokens.items():
            kind = "STRONG" if model == os.getenv("RX_MODEL_STRONG") else "FAST"
            pin, pout = os.getenv(f"RX_PRICE_IN_{kind}"), os.getenv(f"RX_PRICE_OUT_{kind}")
            if not pin or not pout:
                known = False
                continue
            total += tin / 1e6 * float(pin) + (tout + tth) / 1e6 * float(pout)
        return total if known else None


USAGE = Usage()
_last_call = [0.0]


def model_names():
    return os.getenv("RX_MODEL_FAST", "gemini-flash-latest"), os.getenv("RX_MODEL_STRONG", "gemini-pro-latest")


def _client():
    """AI Studio key by default. For Vertex AI set GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT and
    GOOGLE_CLOUD_LOCATION, then run `gcloud auth application-default login`."""
    from google import genai
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
        return genai.Client(vertexai=True, project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is empty. Put a key in .env (see .env.example), or set RX_FAKE=1 for a dry run.")
    from google.genai import types
    timeout_ms = int(float(os.getenv("RX_HTTP_TIMEOUT", "90")) * 1000)
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))


def list_models():
    c = _client()
    return [m.name for m in c.models.list() if "generateContent" in (getattr(m, "supported_actions", None) or [])]


def ask_image(model, image_bytes, prompt, schema, tag, fake_answer=None):
    """Send one image + prompt; return parsed JSON. Cached by (model, prompt, schema, image)."""
    h = hashlib.sha256()
    for part in (model.encode(), prompt.encode(), json.dumps(schema.model_json_schema(), sort_keys=True).encode(), image_bytes):
        h.update(part)
    path = CACHE / tag / f"{h.hexdigest()[:24]}.json"
    if path.exists():
        USAGE.cached += 1
        rec = json.loads(path.read_text(encoding="utf-8"))
        USAGE.add(rec["model"], *rec["tokens"], rec["seconds"])
        return rec["answer"]
    if os.getenv("RX_FAKE") == "1":
        if fake_answer is None:
            raise RuntimeError("fake mode needs fake_answer")
        answer, tokens, secs = fake_answer(), [1500, 200, 0], 0.0
    else:
        if USAGE.calls >= int(os.getenv("RX_MAX_CALLS", "800")):
            raise CallLimitReached(f"RX_MAX_CALLS={os.getenv('RX_MAX_CALLS', '800')} reached; rerun to continue (cached calls are free).")
        answer, tokens, secs = _real_call(model, image_bytes, prompt, schema)
    USAGE.calls += 1
    USAGE.add(model, *tokens, secs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "answer": answer, "tokens": tokens, "seconds": secs}), encoding="utf-8")
    return answer


def _real_call(model, image_bytes, prompt, schema):
    from google.genai import types
    client = _client()
    cfg = dict(response_mime_type="application/json", response_schema=schema)
    if not model.startswith("gemini-3"):  # Gemini 3 models are tuned for the default temperature
        cfg["temperature"] = 0
    if os.getenv("RX_THINKING_BUDGET"):
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=int(os.getenv("RX_THINKING_BUDGET")))
    gap = float(os.getenv("RX_MIN_SECONDS", "6.5"))
    for attempt in range(int(os.getenv("RX_RETRIES", "6"))):
        wait = _last_call[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        t0 = time.time()
        _last_call[0] = t0
        try:
            r = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
                config=types.GenerateContentConfig(**cfg))
            secs = time.time() - t0
            um = r.usage_metadata
            tokens = [getattr(um, "prompt_token_count", 0) or 0, getattr(um, "candidates_token_count", 0) or 0,
                      getattr(um, "thoughts_token_count", 0) or 0]
            answer = r.parsed.model_dump() if getattr(r, "parsed", None) is not None else json.loads(r.text)
            return answer, tokens, secs
        except Exception as e:  # rate limits and transient errors: back off and retry
            msg = str(e)
            if attempt == int(os.getenv("RX_RETRIES", "6")) - 1 or not any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "500", "503", "UNAVAILABLE", "DEADLINE")):
                raise
            print(f"  retry {attempt + 1} on {model}: {msg[:70]}", flush=True)
            time.sleep(min(60, 5 * (2 ** attempt)) + random.random() * 3)
