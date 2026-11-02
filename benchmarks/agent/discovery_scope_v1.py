"""Discovery scope benchmark v1 (model-free).

Builds a website-scale tree (bulk node_modules, dist, reference crawl,
first-party src/docs, .gitignore) in scratch and measures scoped vs
unrestricted discovery: files visited, directories pruned, cold/warm
answer latency, and in-scope result parity. No model calls, no frozen
heldout contact.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tunic.baselines import BM25, TextNotes  # noqa: E402
from tunic.memory import Memory  # noqa: E402

DISCOVERY_VERSION = 1
NODE_MODULES_FILES = 3000
REFERENCE_FILES = 200

QUERIES = {
    "helper": "Where is the helper function defined?",
    "render": "Where is the render function defined?",
    "retention": "What does the retention note say?",
}
GOLD = {
    "helper": "src/util.py::helper",
    "render": "src/app.ts::render",
    "retention": "docs/keep.md",
}
# The retention query needs documents as first-class candidates (R2):
# TextNotes hits only surface through code mentions today, so the gold
# document is indexed but never proposed. Reported, not gated.


def build_site(root, node_files=NODE_MODULES_FILES,
               ref_files=REFERENCE_FILES):
    root = Path(root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "reference").mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(
        "dist/\nreference/\n*.log\n*.md\n!docs/keep.md\n",
        encoding="utf-8")
    (root / "src" / "app.py").write_text(
        "from util import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8")
    (root / "src" / "util.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8")
    (root / "src" / "app.ts").write_text(
        "export function render(name: string): string {\n"
        "  return name;\n}\n",
        encoding="utf-8")
    (root / "docs" / "guide.md").write_text(
        "# Guide\n\nThe `render` function formats a greeting.\n",
        encoding="utf-8")
    (root / "docs" / "keep.md").write_text(
        "# Kept\n\nRetention note: keep seven days.\n", encoding="utf-8")
    (root / "dist" / "bundle.js").write_text(
        "function render(name){return name}\n", encoding="utf-8")
    for index in range(ref_files):
        (root / "reference" / ("crawl-%04d.md" % index)).write_text(
            "# Crawl\n\nVendored crawl mentioning render many times.\n",
            encoding="utf-8")
    pkg = root / "node_modules" / "pkg"
    (pkg / "deep").mkdir(parents=True, exist_ok=True)
    for index in range(node_files):
        target = pkg / ("mod-%04d.js" % index)
        target.write_text("module.exports = {};\n", encoding="utf-8")
    for index in range(50):
        target = pkg / "deep" / ("doc-%04d.md" % index)
        target.write_text("# Vendored\n\nThird-party doc.\n", encoding="utf-8")
    (root / "debug.log").write_text("noise\n", encoding="utf-8")


def _count_all(root):
    total = 0
    for _base, _dirs, files in os.walk(root):
        total += len(files)
    return total


def _top3(answer):
    seen = ([c["symbol"] for c in answer.get("candidates", [])] +
            [i["symbol"] for i in answer.get("impacts", [])])
    return seen[:3]


def run_discovery_benchmark(workdir):
    workdir = Path(workdir)
    site = workdir / "site"
    build_site(site)
    total_files = _count_all(str(site))
    db_path = str(workdir / "bench.sqlite")
    memory = Memory(db_path, str(site))
    try:
        t0 = time.perf_counter()
        ingest_report = memory.ingest()
        ingest_ms = (time.perf_counter() - t0) * 1000.0
        code = BM25(str(site))
        notes = TextNotes(str(site))
        scoped = {
            "bm25_documents": len(code.documents),
            "notes_documents": len(notes.documents),
            "bm25_stats": code.scope_stats,
            "notes_stats": notes.scope_stats,
        }
        cold, warm = {}, {}
        for label, store in (("cold", cold), ("warm", warm)):
            for key, text in QUERIES.items():
                t0 = time.perf_counter()
                answer = memory.answer(text, select=True)
                ms = (time.perf_counter() - t0) * 1000.0
                store[key] = {"ms": round(ms, 1), "top3": _top3(answer)}
        scoped_docs = sorted(d["symbol"] for d in code.documents)
        report = {
            "discovery_version": DISCOVERY_VERSION,
            "model_free": True,
            "total_files_on_disk": total_files,
            "ingest_ms": round(ingest_ms, 1),
            "ingest_report": {
                "sources": ingest_report["sources"],
                "symbols": ingest_report["symbols"],
                "excluded": ingest_report.get("excluded"),
            },
            "scoped": scoped,
            "scoped_bm25_documents": scoped_docs,
            "cold": cold,
            "warm": warm,
            "parity": {
                key: {"gold": GOLD[key],
                      "scoped_hit": GOLD[key] in cold[key]["top3"],
                      "warm_hit": GOLD[key] in warm[key]["top3"],
                      "requires_r2": key == "retention"}
                for key in QUERIES
            },
        }
        return report
    finally:
        memory.close()


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else \
        HERE / "runs" / "discovery-scope-v1-dev.json"
    with tempfile.TemporaryDirectory() as tmp:
        report = run_discovery_benchmark(tmp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True),
                   encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
