"""Generic subscription-CLI inference backend for the agent benchmarks.

Any coding-agent CLI that can run a tool-less agent in non-interactive
print mode, riding the user's own authenticated subscription, can serve
as a benchmark inference backend. No API keys or OAuth tokens are read,
copied, logged, or refreshed here.

A backend is a CLIBackend instance built from a small preset dict:

  name            artifact label, e.g. "kimi-cli"
  exe             executable name on PATH, e.g. "kimi"
  agent_file      tool-less agent spec (tools: [] so the model
                  physically cannot touch the filesystem or shell,
                  regardless of prompt content)
  argv            callable (exe, agent_file, scratch, model, prompt)
                  returning the print-mode argv list
  quota_markers   substrings (case-insensitive) identifying subscription
                  quota exhaustion in CLI output
  identity_hint   callable returning the model label for run records
                  (config-file names only, never credentials)

One subprocess per inference call: a CLI's persistent session would
share agent context across messages and contaminate independent
trials. The scratch work directory is per-run, not per-call, because
CLIs key session state by work directory.

Failure policy: quota exhaustion raises QuotaError immediately (never
retried -- the runner must save progress and exit); exit code 75 and
subprocess timeouts are retried with bounded backoff; any other
non-zero exit, empty output, or exhausted retries raises BackendError
so a run fails loudly instead of recording silent abstentions.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 300
MAX_ATTEMPTS = 3


class BackendError(RuntimeError):
    """The CLI backend could not produce a response."""


class QuotaError(BackendError):
    """Subscription quota exhausted for the current window.

    Terminal: the runner must save progress and exit instead of
    retrying. Never retried inside chat()."""


def _print_mode_argv(exe, agent_file, scratch, model, prompt):
    """Generic print-mode argv for tool-less agent CLIs: quiet, pinned
    agent file, isolated work dir, prompt on -p, optional model flag."""
    argv = [exe, "--quiet", "--agent-file", str(agent_file),
            "--work-dir", str(scratch), "-p", prompt]
    if model:
        argv.extend(["--model", model])
    return argv


def _default_model_from_config(config_path):
    """Best-effort default-model name from a CLI config file -- the
    name only, never any credential."""
    try:
        match = re.search(r'^\s*default_model\s*=\s*"([^"]+)"',
                          Path(config_path).read_text(
                              encoding="utf-8"), re.MULTILINE)
        return match.group(1) if match else None
    except OSError:
        return None


KIMI_PRESET = {
    # First preset of the generic backend, kept under its frozen
    # artifact label: run records say "kimi-cli" and stay comparable.
    "name": "kimi-cli",
    "exe": "kimi",
    "agent_file": Path(__file__).resolve().parent / "ai_agent" / "agent.yaml",
    "argv": _print_mode_argv,
    "quota_markers": ("429", "exceeded_current_quota", "usage limit",
                      "five-hour", "five hour", "quota"),
    "identity_hint": lambda model: "kimi-cli:%s (managed subscription)"
                      % (model or "default-model"),
    "default_model_config": Path.home() / ".kimi" / "config.toml",
}

GENERIC_QUOTA_MARKERS = ("429", "quota", "usage limit")


class CLIBackend:
    """One configured subscription CLI. Instances are independent:
    configuring one never affects another."""

    def __init__(self, name, exe, agent_file, argv=None, model=None,
                 timeout_s=None, quota_markers=None, identity_hint=None,
                 scratch_prefix="tunic-cli-"):
        self.name = name
        self.exe = exe
        self.agent_file = Path(agent_file)
        self.argv = argv or _print_mode_argv
        self.model = model
        self.timeout_s = int(timeout_s) if timeout_s else DEFAULT_TIMEOUT_S
        self.quota_markers = tuple(quota_markers) if quota_markers \
            else GENERIC_QUOTA_MARKERS
        self.identity_hint = identity_hint or (lambda m: "%s:%s" % (name, m or "default-model"))
        self.scratch_prefix = scratch_prefix
        self._scratch = None

    def configure(self, model=None, timeout_s=None):
        """Optional per-run overrides. model=None keeps the preset default."""
        self.model = model
        if timeout_s:
            self.timeout_s = int(timeout_s)

    def identity(self):
        """Best-effort backend label for the run record."""
        return self.identity_hint(self.model)

    def is_quota_text(self, text):
        """True when CLI output reports quota exhaustion rather than a
        transient failure (case-insensitive; callers pass stdout+stderr)."""
        lowered = (text or "").lower()
        return any(marker in lowered for marker in self.quota_markers)

    def _scratch_dir(self):
        if self._scratch is None:
            self._scratch = Path(tempfile.mkdtemp(prefix=self.scratch_prefix))
        return self._scratch

    @staticmethod
    def _kill_tree(proc):
        """Kill the process tree. On Windows the CLI may be a launcher
        with child processes, so taskkill /T is required; elsewhere
        kill the group and let the shell reap children."""
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()

    def _invoke(self, prompt, timeout_s):
        exe = shutil.which(self.exe)
        if not exe:
            raise BackendError("%s executable not found on PATH" % self.exe)
        argv = self.argv(exe, self.agent_file, self._scratch_dir(),
                         self.model, prompt)
        started = time.monotonic()
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
            proc.communicate()
            raise BackendError("%s timed out after %ds" % (self.exe, timeout_s))
        latency_ms = (time.monotonic() - started) * 1000.0
        return proc.returncode, out, err, latency_ms

    def chat(self, prompt):
        """One inference call. Returns the same shape as the Ollama chat()
        minus token counts: CLI interfaces do not report usage, and
        unavailable counts are omitted (never fabricated)."""
        last = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                code, out, err, latency_ms = self._invoke(
                    prompt, self.timeout_s)
            except BackendError as error:
                last = error
                if attempt < MAX_ATTEMPTS:
                    time.sleep(min(30, 5 * (2 ** (attempt - 1))))
                    continue
                raise
            if code == 0 and out.strip():
                return {"text": out, "latency_ms": latency_ms}
            if self.is_quota_text(out + "\n" + (err or "")):
                raise QuotaError(
                    "%s quota exhausted (exit=%s stderr_tail=%r); "
                    "save progress and retry after the quota window"
                    % (self.exe, code, (err or "")[-400:]))
            last = BackendError(
                "%s exit=%s stdout=%r stderr_tail=%r"
                % (self.exe, code, out[-200:], (err or "")[-400:]))
            if code == 75 and attempt < MAX_ATTEMPTS:
                time.sleep(min(30, 5 * (2 ** (attempt - 1))))
                continue
            raise last
        raise last


def ai_backend(model=None, timeout_s=None, preset=None):
    """A subscription-CLI inference backend from a preset dict.

    Defaults to the kimi-cli preset (first preset; artifact labels
    say "kimi-cli"). Pass a preset dict to target another assistant CLI.
    """
    preset = dict(preset or KIMI_PRESET)
    config_path = preset.pop("default_model_config", None)
    backend = CLIBackend(**{
        key: value for key, value in preset.items()})
    if model is None and config_path is not None:
        model = _default_model_from_config(config_path)
    backend.configure(model=model, timeout_s=timeout_s)
    return backend


def kimi_backend(model=None, timeout_s=None):
    """Deprecated alias: the kimi-cli preset of ai_backend.

    Kept so frozen runners and run records keep working; new code
    passes preset=KIMI_PRESET (or another preset) to ai_backend.
    """
    return ai_backend(model=model, timeout_s=timeout_s,
                      preset=KIMI_PRESET)
