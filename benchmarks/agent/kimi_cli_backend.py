"""Kimi Code CLI inference backend for the agent benchmarks.

Invokes the installed `kimi` CLI in non-interactive print mode, riding the
user's existing authenticated Kimi Code subscription (OAuth). No API keys
or OAuth tokens are read, copied, logged, or refreshed here.

One subprocess per inference call, run inside the tool-less agent spec in
kimi_agent/ (tools: [] -- the model physically cannot touch the filesystem
or shell, regardless of prompt content). The scratch work directory is
per-run, not per-call: the CLI keys session state by work directory, and
hundreds of unique directories would litter ~/.kimi/sessions.

The CLI's persistent --input-format=stream-json session was rejected on
independence grounds: every message fed to one process shares a single
agent context, so later benchmark trials would observe earlier ones.

Failure policy: exit code 75 (transient: rate limit / 5xx / timeout) and
subprocess timeouts are retried with bounded backoff; any other non-zero
exit, empty output, or exhausted retries raises BenchmarkModelError so a
run fails loudly instead of recording silent abstentions.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT_FILE = Path(__file__).resolve().parent / "kimi_agent" / "agent.yaml"

DEFAULT_TIMEOUT_S = 300
MAX_ATTEMPTS = 3

_config = {"model": None, "timeout_s": DEFAULT_TIMEOUT_S}
_scratch = None


class BenchmarkModelError(RuntimeError):
    """The Kimi CLI backend could not produce a response."""


def configure(model=None, timeout_s=None):
    """Optional per-run overrides. model=None uses the config-file default."""
    _config["model"] = model
    if timeout_s:
        _config["timeout_s"] = int(timeout_s)


def model_identity():
    """Best-effort backend identity for the run record. Reads only the
    default_model name from ~/.kimi/config.toml -- never any credential."""
    name = _config["model"]
    if not name:
        cfg = Path.home() / ".kimi" / "config.toml"
        try:
            match = re.search(r'^\s*default_model\s*=\s*"([^"]+)"',
                              cfg.read_text(encoding="utf-8"), re.MULTILINE)
            name = match.group(1) if match else None
        except OSError:
            name = None
    return "kimi-cli:%s (managed subscription)" % (name or "default-model")


def _scratch_dir():
    global _scratch
    if _scratch is None:
        _scratch = Path(tempfile.mkdtemp(prefix="tunic-kimi-"))
    return _scratch


def _kill_tree(proc):
    """Kill the process tree. kimi.exe is a launcher with child python
    processes on Windows, so taskkill /T is required; elsewhere kill the
    group and let the shell reap children."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.kill()


def _invoke(prompt, timeout_s):
    exe = shutil.which("kimi")
    if not exe:
        raise BenchmarkModelError("kimi executable not found on PATH")
    argv = [exe, "--quiet", "--agent-file", str(AGENT_FILE),
            "--work-dir", str(_scratch_dir()), "-p", prompt]
    if _config["model"]:
        argv.extend(["--model", _config["model"]])
    started = time.monotonic()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        raise BenchmarkModelError("kimi CLI timed out after %ds" % timeout_s)
    latency_ms = (time.monotonic() - started) * 1000.0
    return proc.returncode, out, err, latency_ms


def chat(prompt):
    """One inference call. Returns the same shape as the Ollama chat()
    minus token counts: the kimi-cli interface does not report usage, and
    unavailable counts are omitted (never fabricated)."""
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            code, out, err, latency_ms = _invoke(prompt, _config["timeout_s"])
        except BenchmarkModelError as error:
            last = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(30, 5 * (2 ** (attempt - 1))))
                continue
            raise
        if code == 0 and out.strip():
            return {"text": out, "latency_ms": latency_ms}
        last = BenchmarkModelError(
            "kimi CLI exit=%s stdout=%r stderr_tail=%r"
            % (code, out[-200:], (err or "")[-400:]))
        if code == 75 and attempt < MAX_ATTEMPTS:
            time.sleep(min(30, 5 * (2 ** (attempt - 1))))
            continue
        raise last
    raise last


if __name__ == "__main__":
    response = chat(sys.stdin.read())
    print(response["text"])
    print("latency_ms=%.0f" % response["latency_ms"], file=sys.stderr)
