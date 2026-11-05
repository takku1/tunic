"""Score Tunic's static edges against calls the interpreter actually made.

Input is trace_calls.py output. Labels are independent of Tunic.

    recall     of runtime calls between indexed symbols, the share the
               static graph also holds. Misses are real: the call happened.
    confirmed  of static edges whose caller ran, the share seen at runtime.
               This is a floor, because a branch that tests never took is
               not a wrong edge.

Hints are scored the same way, so proof and interpretation can be compared
on one scale.
"""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tunic.baselines import used_names  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.pack import _excerpt  # noqa: E402


def main(trace_path):
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    runtime = {tuple(pair) for pair in trace["pairs"]}
    executed = set(trace["executed"])
    with tempfile.TemporaryDirectory(prefix="tunic-edges-") as work:
        with Memory(str(Path(work) / "m.sqlite"), ROOT / "tunic") as memory:
            memory.ingest()
            snapshot, view = memory.store.view()
            index = memory._pack_index(snapshot, view)
            sources = memory.store.snapshot_sources(snapshot)
            static = {(caller, callee) for caller, callees in index.calls.items()
                      for callee in callees}
            hints = set()
            for key in sorted(executed):
                record = index.symbols.get(key)
                if record is None or record["source"] not in sources:
                    continue
                text, _cut = _excerpt(sources[record["source"]][1], record,
                                      index.starts.get(record["path"], ()))
                hints |= {(key, index.unique[word]) for word in used_names(text)
                          if word in index.unique and index.unique[word] != key}
            known = set(index.symbols)
    observable = {pair for pair in runtime if pair[0] in known and pair[1] in known}
    ran = {pair for pair in static if pair[0] in executed}
    hint_ran = {pair for pair in hints if pair[0] in executed}
    union = static | hints
    missed = sorted(observable - static)
    print("runtime calls between indexed symbols: %d" % len(observable))
    print("static edges: %d, with caller executed: %d" % (len(static), len(ran)))
    print("static recall    %.3f" % (len(observable & static) / len(observable)))
    print("static confirmed %.3f (floor)" % (len(ran & runtime) / len(ran)))
    print("hint recall      %.3f" % (len(observable & hints) / len(observable)))
    print("hint confirmed   %.3f (floor)" % (len(hint_ran & runtime) / len(hint_ran)))
    print("static+hint recall %.3f" % (len(observable & union) / len(observable)))
    print("sample of runtime calls with no static edge:")
    for caller, callee in missed[:12]:
        print("  %s -> %s" % (caller, callee))


if __name__ == "__main__":
    main(sys.argv[1])
