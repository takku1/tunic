"""Record calls that actually happen, as ground truth independent of Tunic.

Every other edge benchmark here labels fixtures by hand or derives gold
from Tunic's own output. This one runs real code under a profiler, so
the labels come from the interpreter. It executes only this repository's
own test suite, never an analyzed repository's code.

    python benchmarks/integrity/trace_calls.py <out.json> [pytest args]

Writes [[caller, callee], ...] as path::qualname keys relative to tunic/,
plus the set of functions that executed. Closures and lambdas are left
out because the static profile attributes nothing to them.
"""

import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = str(ROOT / "tunic") + os.sep
sys.path.insert(0, str(ROOT))

pairs, executed, names = set(), set(), {}


def key_of(code):
    """path::qualname for a named function in the package, else None."""
    cached = names.get(code, False)
    if cached is not False:
        return cached
    key = None
    filename = code.co_filename
    if filename.startswith(PACKAGE) and "<" not in code.co_qualname:
        relative = filename[len(PACKAGE):].replace(os.sep, "/")
        key = relative + "::" + code.co_qualname
    names[code] = key
    return key


def profile(frame, event, _arg):
    if event != "call":
        return
    callee = key_of(frame.f_code)
    if callee is None:
        return
    executed.add(callee)
    parent = frame.f_back
    caller = key_of(parent.f_code) if parent is not None else None
    if caller is not None:
        pairs.add((caller, callee))


def main():
    import pytest
    out = sys.argv[1]
    os.chdir(ROOT)
    threading.setprofile(profile)
    sys.setprofile(profile)
    try:
        status = pytest.main(sys.argv[2:] + ["-q", "-p", "no:cacheprovider",
                                             "-p", "no:randomly"])
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
    Path(out).write_text(json.dumps({"pytest_status": int(status),
                                     "pairs": sorted(pairs),
                                     "executed": sorted(executed)}), encoding="utf-8")
    print("pairs", len(pairs), "executed", len(executed), "status", int(status))


if __name__ == "__main__":
    main()
