"""Pinned loopback Ollama adapter. No downloads, remote endpoints or silent truncation."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .identity import digest, identity
from .render import TEMPLATE, QWEN_TEMPLATE


class BackendError(ValueError):
    def __init__(self, message, *, record=None):
        super().__init__(message)
        self.record = record


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BackendError("backend redirect is not allowed")


class Ollama:
    def __init__(self, endpoint="http://127.0.0.1:11434", *, timeout=60):
        parsed = urllib.parse.urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ValueError("only a loopback HTTP Ollama origin is supported")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _open(self, path, body=None):
        request = urllib.request.Request(self.endpoint + path,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            raise BackendError(f"Ollama HTTP {error.code}: {detail}") from error
        except OSError as error:
            raise BackendError(f"Ollama connection failed: {error}") from error

    def _json(self, path, body=None):
        with self._open(path, body) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise BackendError("backend metadata response too large")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise BackendError("invalid backend JSON") from error
        if not isinstance(result, dict) or result.get("error"):
            raise BackendError("backend returned an error or invalid metadata")
        return result

    def models(self):
        return [{"name": m["name"], "digest": m["digest"]} for m in self._json("/api/tags")["models"]]

    def inspect(self, model):
        installed = next((m for m in self.models() if m["name"] == model), None)
        if installed is None:
            raise BackendError("pinned model is missing; it may have been replaced. Refresh `models`, repin, and restart the comparison")
        version = self._json("/api/version").get("version")
        show = self._json("/api/show", {"model": model, "verbose": False})
        details = show.get("details", {})
        info = show.get("model_info", {})
        family = details.get("family")
        if model.startswith("llama3.2:") and family == "llama":
            template, context_key = TEMPLATE, "llama.context_length"
        elif model.startswith("qwen3:") and family == "qwen3":
            template, context_key = QWEN_TEMPLATE, "qwen3.context_length"
        else:
            raise BackendError("unsupported_lowering: requires an installed llama3.2 or dense qwen3 GGUF model")
        if details.get("format") != "gguf" or context_key not in info:
            raise BackendError("unsupported model format or missing context metadata")
        if not isinstance(version, str) or not installed["digest"]:
            raise BackendError("backend did not identify model/server")
        return {"backend": "ollama", "endpoint": self.endpoint, "server_version": version,
                "model": model, "model_digest": installed["digest"], "details": details,
                "model_context_length": info[context_key], "template": template,
                "metadata_digest": identity("ollama-metadata", {k:show.get(k) for k in
                                      ("details", "model_info", "template", "parameters", "capabilities")})}

    def pin(self, model):
        profile = self.inspect(model)
        # Capability probe: the installed tokenizer must reject this at a small
        # context limit, not silently truncate it. This is a real inference request.
        try:
            self._stream(profile, "hello " * 2048, num_ctx=256, num_predict=1, seed=7)
        except BackendError as error:
            message = str(error).lower()
            if "http 400" not in message or "exceeds" not in message or "context" not in message:
                raise BackendError("could not verify nontruncating overflow behavior: " + str(error)) from error
        else:
            raise BackendError("unsupported_lowering: backend accepted oversized input with truncation disabled")
        if self.inspect(model) != profile:
            raise BackendError("model changed during pinning; refresh models and repin")
        result = {"profile_version": "tunic.ollama.raw.v1", **profile,
                  "nontruncating_overflow_verified": True,
                  "counting": "one_token_inference_probe", "cache_mode": "backend_managed_not_controlled"}
        result["id"] = identity("backend-profile", result)
        return result

    def check_profile(self, profile):
        payload = {k:v for k,v in profile.items() if k != "id"}
        if (profile.get("id") != identity("backend-profile", payload)
                or profile.get("profile_version") != "tunic.ollama.raw.v1"
                or profile.get("nontruncating_overflow_verified") is not True):
            raise BackendError("invalid pinned backend profile")
        actual = self.inspect(profile["model"])
        if any(actual[k] != profile.get(k) for k in actual):
            raise BackendError("model/server/profile changed; refresh models, repin, and restart the comparison")

    def _stream(self, profile, prompt, *, num_ctx, num_predict, seed, output_schema=None):
        body = {"model": profile["model"], "prompt": prompt, "raw": True, "stream": True,
                "truncate": False, "shift": False, "keep_alive": "5m",
                "options": {"num_ctx": num_ctx, "num_predict": num_predict, "temperature": 0,
                            "seed": seed, "stop": ["<|eot_id|>", "<|end_of_text|>"]}}
        if output_schema is not None:
            body["format"] = output_schema
        if profile["template"] == QWEN_TEMPLATE:
            body["think"] = False
            body["options"]["stop"] = ["<|im_end|>", "<|endoftext|>"]
        start = time.perf_counter()
        first = None
        chunks, final = [], None
        try:
            with self._open("/api/generate", body) as response:
                while True:
                    line = response.readline(1024 * 1024 + 1)
                    if not line:
                        break
                    if len(line) > 1024 * 1024 or time.perf_counter()-start > self.timeout:
                        raise BackendError("generation stream exceeds bounds")
                    item = json.loads(line)
                    if not isinstance(item, dict) or item.get("error"):
                        raise BackendError("generation stream returned an error")
                    if item.get("model") != profile["model"]:
                        raise BackendError("generation response model differs from pin")
                    text = item.get("response", "")
                    if not isinstance(text, str):
                        raise BackendError("invalid generation text")
                    if text and first is None:
                        first = time.perf_counter()-start
                    chunks.append(text)
                    if sum(map(len, chunks)) > 1024 * 1024:
                        raise BackendError("generation text exceeds bound")
                    if item.get("done") is True:
                        final = item
                        break
        except (OSError, ValueError) as error:
            if isinstance(error, BackendError):
                raise
            raise BackendError("failed or malformed generation stream: " + str(error)) from error
        if final is None:
            raise BackendError("incomplete generation stream; no answer accepted")
        for field in ("prompt_eval_count", "eval_count", "prompt_eval_duration", "eval_duration", "total_duration", "load_duration"):
            if type(final.get(field)) is not int or final[field] < 0:
                raise BackendError("missing/invalid backend metric: " + field)
        if final["eval_count"] > num_predict:
            raise BackendError("backend exceeded requested output budget")
        if final.get("done_reason") not in {"stop", "length"}:
            raise BackendError("unsupported generation completion reason")
        metrics = {k:final.get(k) for k in ("prompt_eval_count", "prompt_eval_cached_count", "eval_count",
                    "prompt_eval_duration", "eval_duration", "total_duration", "load_duration", "done_reason")}
        cached = metrics["prompt_eval_cached_count"]
        if cached is not None and (type(cached) is not int or not 0 <= cached <= metrics["prompt_eval_count"]):
            raise BackendError("invalid cached-token counter")
        return {"text": "".join(chunks), "metrics": metrics,
                "http_wall_seconds": time.perf_counter()-start, "first_response_seconds": first}

    def generate(self, profile, prompt, *, context_tokens=8192, max_input_tokens=6000,
                 output_tokens=192, seed=7, max_prompt_bytes=131072, output_schema=None):
        for name, value in (("context_tokens", context_tokens), ("max_input_tokens", max_input_tokens),
                            ("output_tokens", output_tokens), ("max_prompt_bytes", max_prompt_bytes)):
            if type(value) is not int or value < 1:
                raise ValueError(name + " must be a positive integer")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be nonempty text")
        if len(prompt.encode("utf-8")) > max_prompt_bytes:
            raise BackendError("budget_unsatisfied: complete prompt exceeds byte bound")
        if context_tokens < 256 or context_tokens > profile["model_context_length"] or output_tokens >= context_tokens:
            raise BackendError("budget_unsatisfied: invalid context/output reservation")
        record = {"profile": profile["id"], "prompt_digest": digest(prompt.encode("utf-8")),
                  "prompt_bytes": len(prompt.encode("utf-8")), "template": profile["template"],
                  "options": {"context_tokens": context_tokens, "max_input_tokens": max_input_tokens,
                              "output_tokens": output_tokens, "seed": seed, "temperature": 0},
                  "cache_mode": "backend_managed_not_controlled", "probe_affects_cache": True}
        record["output_schema"] = output_schema
        start = time.perf_counter()
        try:
            self.check_profile(profile)
            probe = self._stream(profile, prompt, num_ctx=context_tokens, num_predict=1, seed=seed, output_schema=output_schema)
            record["counting_probe"] = {k:v for k,v in probe.items() if k != "text"}
            record["counting_probe"]["discarded_output_digest"] = digest(probe["text"].encode("utf-8"))
            count = probe["metrics"]["prompt_eval_count"]
            record["actual_input_tokens"] = count
            if count < 1 or count > max_input_tokens or count + output_tokens > context_tokens:
                raise BackendError("budget_unsatisfied: exact prompt count plus output reservation does not fit")
            self.check_profile(profile)
            generation = self._stream(profile, prompt, num_ctx=context_tokens, num_predict=output_tokens, seed=seed, output_schema=output_schema)
            record["generation"] = generation
            if generation["metrics"]["prompt_eval_count"] != count:
                raise BackendError("input count changed between probe and generation; possible truncation or backend drift")
            self.check_profile(profile)
            record["wall_seconds_including_probe_and_pin_checks"] = time.perf_counter()-start
            record["outcome"] = "complete" if generation["metrics"]["done_reason"] == "stop" else "output_limit_reached"
            return record
        except (BackendError, OSError) as error:
            record["wall_seconds_including_probe_and_pin_checks"] = time.perf_counter()-start
            record["outcome"] = "failed"
            record["error"] = str(error)
            raise BackendError(str(error), record=record) from error
