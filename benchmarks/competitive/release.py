"""Content-addressed benchmark inventory; lock once, verify on every run."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def confined(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("missing or unconfined benchmark input: " + relative)
    return path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def inventory(root):
    root = Path(root)
    manifest = load(root / "manifest.json")
    tasks = load(confined(root, manifest["tasks"]))
    gold = load(confined(root, manifest["gold"]))
    ids = [task["id"] for task in tasks]
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(gold):
        raise ValueError("tasks and gold must have the same unique nonempty IDs")
    events = manifest["events"]
    if len(events) != len(set(events)) or not events:
        raise ValueError("events must be nonempty and unique")
    for task in tasks:
        if (type(task["after_event"]) is not int
                or not 1 <= task["after_event"] <= len(events)
                or not task["question"].strip() or not task["category"]):
            raise ValueError("invalid task: " + task["id"])
        expected = gold[task["id"]]
        for field in ("answer", "evidence"):
            values = expected[field]
            if (not isinstance(values, list) or len(values) != len(set(values))
                    or not all(isinstance(v, str) and v for v in values)):
                raise ValueError("invalid gold " + field)
        if not set(expected["evidence"]) <= set(events[:task["after_event"]]):
            raise ValueError("gold cites future or missing evidence: " + task["id"])
    paths = {".gitattributes", "manifest.json", manifest["tasks"], manifest["gold"],
             "release.py", "score.py", "run_tunic.py", *events}
    actual_corpus = {p.relative_to(root).as_posix()
                     for p in (root / "corpus").rglob("*") if p.is_file()}
    if actual_corpus != set(events):
        raise ValueError("corpus inventory differs from declared events")
    return {relative: sha(confined(root, relative).read_bytes())
            for relative in sorted(paths)}


def create_lock(root=ROOT):
    root = Path(root)
    files = inventory(root)
    receipt = {"schema": "tunic.benchmark-lock/1", "files": files,
               "digest": sha(canonical(files))}
    # Refuse replacement: changing an established exam requires a new release.
    with (root / "release.lock.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def verify(root=ROOT):
    root = Path(root)
    receipt = load(root / "release.lock.json")
    if receipt.get("schema") != "tunic.benchmark-lock/1":
        raise ValueError("unknown lock schema")
    # Verification hashes the labels as opaque bytes. The retrieval adapter
    # never parses gold; only release creation and the scorer interpret it.
    actual = {relative: sha(confined(root, relative).read_bytes())
              for relative in receipt["files"]}
    manifest = load(root / "manifest.json")
    actual_corpus = {p.relative_to(root).as_posix()
                     for p in (root / "corpus").rglob("*") if p.is_file()}
    if actual_corpus != set(manifest["events"]):
        raise ValueError("corpus inventory differs from declared events")
    if actual != receipt["files"] or sha(canonical(actual)) != receipt["digest"]:
        raise ValueError("benchmark drift: locked inputs or scoring changed")
    return receipt["digest"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("lock", "verify"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(create_lock(args.root)["digest"] if args.action == "lock" else verify(args.root))


if __name__ == "__main__":
    main()
