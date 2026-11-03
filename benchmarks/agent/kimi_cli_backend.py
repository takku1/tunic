"""kimi-cli preset of the generic subscription-CLI backend.

Backward-compatible shim: existing runners import configure/chat/
model_identity and the BenchmarkModelError/BenchmarkQuotaError names
from here. New code should use cli_backend.CLIBackend directly (any
subscription CLI can be a preset; kimi-cli is just the first one).
"""
from cli_backend import (  # noqa: F401,E402
    BackendError as BenchmarkModelError,
    CLIBackend,
    QuotaError as BenchmarkQuotaError,
    ai_backend,
)

_backend = ai_backend()


def configure(model=None, timeout_s=None):
    """Optional per-run overrides. model=None uses the config-file default."""
    _backend.configure(model=model, timeout_s=timeout_s)


def model_identity():
    """Best-effort backend identity for the run record."""
    return _backend.identity()


def is_quota_text(text):
    """True when CLI output reports quota exhaustion (case-insensitive)."""
    return _backend.is_quota_text(text)


def chat(prompt):
    """One inference call via the configured assistant CLI (tool-less agent)."""
    return _backend.chat(prompt)


if __name__ == "__main__":
    import sys
    response = chat(sys.stdin.read())
    print(response["text"])
    print("latency_ms=%.0f" % response["latency_ms"], file=sys.stderr)
