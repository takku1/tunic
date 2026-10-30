"""Public W1 regression suite with independent labels and optional real inference."""

import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import time
import uuid

from .baselines import BM25, direct_callers
from .compiler import compile_impact, validate_artifact
from .continuation import assess_answer, compare_continuation
from .identity import digest, identity
from .render import render_context
from .store import GraphStore


def set_scores(predicted, expected, *, available=True):
    predicted, expected = set(predicted), set(expected)
    tp, fp, fn = len(predicted & expected), len(predicted-expected), len(expected-predicted)
    return {"precision": tp/(tp+fp) if tp+fp else None, "recall": tp/(tp+fn) if tp+fn else None,
            "f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
            "exact_match": available and predicted == expected,
            "tp": tp, "fp": fp, "fn": fn, "predicted": sorted(predicted), "expected": sorted(expected),
            "missing": sorted(expected-predicted), "unexpected": sorted(predicted-expected), "available": available}


def score_response(rendered, text, expected, *, generation_complete=True):
    """Gold never participates in prompt construction or the evidence gate."""
    support = assess_answer(rendered, text)
    claims, predicted, schema_valid = [], [], False
    try:
        def unique(pairs):
            value = {}
            for key,item in pairs:
                if key in value:
                    raise ValueError("duplicate response field")
                value[key] = item
            return value
        parsed = json.loads(text, object_pairs_hook=unique)
        if isinstance(parsed, dict) and set(parsed) == {"coverage", "impacts"} and parsed["coverage"] == "partial" and isinstance(parsed.get("impacts"), list):
            claims = parsed["impacts"]
            predicted = [c["symbol"] for c in claims if isinstance(c, dict) and isinstance(c.get("symbol"), str)]
            schema_valid = all(isinstance(c, dict) and set(c) == {"symbol", "kind", "path", "citations"}
                and isinstance(c["symbol"], str) and c["kind"] == "possible_impact"
                and isinstance(c["path"], list) and c["path"] and all(isinstance(s, str) for s in c["path"])
                and isinstance(c["citations"], list) and c["citations"] and all(isinstance(s, str) for s in c["citations"])
                for c in claims)
    except (ValueError, TypeError):
        pass
    supported = sum(assess_answer(rendered, json.dumps({"coverage": "partial", "impacts": [claim]}))["accepted"]
                    for claim in claims)
    quality = set_scores(predicted, expected, available=generation_complete and schema_valid)
    accepted = generation_complete and schema_valid and support["accepted"]
    return {"gold": quality, "accepted_gold": set_scores(predicted if accepted else [], expected, available=accepted),
            "support_gate": support,
            "support_precision": supported/len(claims) if claims else None,
            "supported_claims": supported, "returned_claims": len(claims),
            "generation_complete": generation_complete, "response_schema_valid": schema_valid,
            "task_complete": generation_complete and support["accepted"] and quality["exact_match"]}


def _inside(root, name):
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or any(p in {"..", "."} for p in relative.parts)
            or "\\" in name or ":" in name):
        raise ValueError("benchmark paths must be contained relative POSIX paths")
    path = (root/name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("benchmark path escapes its root")
    return path


def load_suite(path):
    path = Path(path).resolve(strict=True)
    suite = json.loads(path.read_text(encoding="utf-8-sig"))
    if suite.get("version") != "tunic.w1.benchmark.v1" or not suite.get("cases"):
        raise ValueError("unsupported or empty benchmark suite")
    ids, files = set(), {}
    for case in suite["cases"]:
        if not isinstance(case.get("id"), str) or case["id"] in ids or not case["id"].replace("_", "").isalnum():
            raise ValueError("case IDs must be unique simple names")
        ids.add(case["id"])
        source = _inside(path.parent, case["root"])
        if not source.is_dir() or source.is_symlink():
            raise ValueError("fixture source directory missing or symbolic")
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ValueError("fixture symlinks are not supported")
            if item.is_file() and item.suffix == ".py":
                files[item.relative_to(path.parent).as_posix()] = digest(item.read_bytes())
        if case.get("category") not in {"core", "known_gap"} or "::" not in case.get("target", ""):
            raise ValueError("invalid benchmark category or target")
        stages = [case, *case.get("updates", [])]
        stage_ids = {"initial"}
        for stage in stages:
            for field in ("expected_impacts", "expected_tests"):
                labels = stage.get(field)
                if not isinstance(labels, list) or any(not isinstance(s, str) or "::" not in s for s in labels) or len(set(labels)) != len(labels):
                    raise ValueError("gold labels must be unique explicit symbol lists")
            if not set(stage["expected_tests"]) <= set(stage["expected_impacts"]):
                raise ValueError("gold tests must be a subset of gold impacts")
            edges = stage.get("expected_edges")
            if (not isinstance(edges, list) or any(not isinstance(e, list) or len(e) != 2
                    or any(not isinstance(s, str) or "::" not in s for s in e) for e in edges)
                    or len({tuple(e) for e in edges}) != len(edges)):
                raise ValueError("gold edges must be unique explicit caller/callee pairs")
            reachable = {case["target"]}
            while True:
                expanded = reachable | {a for a,b in edges if b in reachable}
                if expanded == reachable:
                    break
                reachable = expanded
            if reachable-{case["target"]} != set(stage["expected_impacts"]):
                raise ValueError("independent edge and impact labels disagree")
            if stage is not case:
                if stage.get("id") in stage_ids or not isinstance(stage.get("id"), str) or not stage["id"].replace("_", "").isalnum():
                    raise ValueError("update IDs must be unique simple names")
                stage_ids.add(stage["id"])
                for name in [*stage.get("writes", {}), *stage.get("deletes", [])]:
                    _inside(source, name)
                    if not name.endswith(".py"):
                        raise ValueError("updates only modify Python fixture files")
    return suite, {"id": identity("benchmark-dataset", {"suite": suite, "files": files}),
                   "suite_digest": digest(path.read_bytes()), "files": files}


def aggregate(rows):
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    return {"tasks": len(rows), "failed_tasks": sum(not r["available"] for r in rows),
            "micro_precision": tp/(tp+fp) if tp+fp else None, "micro_recall": tp/(tp+fn) if tp+fn else None,
            "micro_f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
            "exact_tasks": sum(r["exact_match"] for r in rows),
            "exact_task_rate": sum(r["exact_match"] for r in rows)/len(rows) if rows else None,
            "tp": tp, "fp": fp, "fn": fn}


def run_benchmark(suite_path, *, top_k=5, backend=None, profile=None, model_cases=None,
                  model_repeats=1, answer_tokens=768, scratch_root=None):
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be positive")
    suite_path = Path(suite_path).resolve(strict=True)
    suite, dataset = load_suite(suite_path)
    if (backend is None) != (profile is None):
        raise ValueError("backend and profile must be supplied together")
    if type(model_repeats) is not int or not 1 <= model_repeats <= 10:
        raise ValueError("model_repeats must be between 1 and 10")
    chosen = set(model_cases) if model_cases is not None else {c["id"] for c in suite["cases"]}
    if not chosen <= {c["id"] for c in suite["cases"]}:
        raise ValueError("unknown model case selector")
    scratch_root = Path(scratch_root or Path.cwd()/".tunic"/"benchmark-runs").resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = scratch_root/("run-"+uuid.uuid4().hex)
    scratch.mkdir()
    code_root = Path(__file__).resolve().parent
    result = {"benchmark": suite["version"], "dataset": dataset, "label_policy": suite["label_policy"],
              "implementation": {p.name:digest(p.read_bytes()) for p in sorted(code_root.glob("*.py"))},
              "environment": {"python": platform.python_version(), "platform": platform.platform()},
              "parameters": {"top_k": top_k, "model_repeats": model_repeats, "answer_tokens": answer_tokens,
                             "model_cases": sorted(chosen) if backend is not None else []},
              "model_profile": profile, "tasks": [], "scope": "public_regression_not_held_out_or_competitor_ranking",
              "baselines": {"bm25": "function chunks + module imports; k1=1.2 b=0.75; query target name/file stem",
                            "direct": "one-hop caller control sharing Tunic extraction", "graph": "full current Tunic compiler"},
              "cache_policy": "model-free not applicable; optional Ollama managed cache/probes uncontrolled",
              "limitations": ["Manually authored labels are not independent human review.",
                              "No dense/vector RAG, W2/W3 or external-product arm is executed.",
                              "Tiny synthetic source fixtures do not establish real-repository or memory quality."]}
    started = time.perf_counter()
    model_stopped = False
    try:
        for case in suite["cases"]:
            case_dir = scratch/case["id"]
            root = case_dir/"source"
            shutil.copytree(_inside(suite_path.parent, case["root"]), root,
                            ignore=shutil.ignore_patterns("__pycache__"))
            with GraphStore(case_dir/"graph.sqlite") as store:
                for number, stage in enumerate([case, *case.get("updates", [])]):
                    row = {"case": case["id"], "stage": "initial" if number == 0 else stage["id"],
                           "category": case["category"], "target": case["target"],
                           "gold": {"impacts": stage["expected_impacts"], "tests": stage["expected_tests"], "edges": stage["expected_edges"]},
                           "outcome": "running", "timings": {}}
                    result["tasks"].append(row)
                    try:
                        if number:
                            for name in stage.get("deletes", []):
                                _inside(root, name).unlink()
                            for name, content in stage.get("writes", {}).items():
                                target = _inside(root, name)
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(content.encode("utf-8"))
                        start = time.perf_counter()
                        capture = store.ingest(root)
                        row["timings"]["graph_build_seconds"] = time.perf_counter()-start
                        row["capture"] = capture
                        start = time.perf_counter()
                        artifact = compile_impact(store, case["target"])
                        row["timings"]["graph_query_compile_seconds"] = time.perf_counter()-start
                        row["ir"] = artifact["id"]
                        row["graph"] = set_scores([i["symbol"] for i in artifact["impacts"]], stage["expected_impacts"])
                        row["graph_tests"] = set_scores([i["symbol"] for i in artifact["impacts"] if i["is_test"]], stage["expected_tests"])
                        _, view = store.view(capture["snapshot"])
                        extracted_edges = [(a["proposition"]["caller"], a["proposition"]["callee"]) for a in view["assertions"]]
                        row["edges"] = set_scores(extracted_edges, [tuple(e) for e in stage["expected_edges"]])
                        gold_edges = {tuple(e) for e in stage["expected_edges"]}
                        assertions = {u["id"]:u["assertion"]["proposition"] for u in artifact["units"]}
                        supported_paths = sum(all((assertions[p]["caller"], assertions[p]["callee"]) in gold_edges
                            for p in i["derivation"]["premises"]) for i in artifact["impacts"])
                        row["path_support_against_gold"] = {"supported": supported_paths, "returned": len(artifact["impacts"]),
                            "precision": supported_paths/len(artifact["impacts"]) if artifact["impacts"] else None}
                        row["validation"] = validate_artifact(store, artifact, expected_snapshot=capture["snapshot"])
                        row["rendered"] = render_context(store, artifact, timings=row["timings"])
                        with GraphStore(case_dir/f"fresh-{number}.sqlite") as fresh:
                            fresh.ingest(root)
                            row["fresh_rebuild_equal"] = artifact == compile_impact(fresh, case["target"])
                        start = time.perf_counter()
                        direct = direct_callers(store, case["target"], capture["snapshot"])
                        row["timings"]["direct_query_seconds"] = time.perf_counter()-start
                        row["direct"] = set_scores(direct, stage["expected_impacts"])
                        start = time.perf_counter()
                        lexical = BM25(root)
                        row["timings"]["bm25_build_seconds"] = time.perf_counter()-start
                        start = time.perf_counter()
                        ranking = lexical.search(case["target"], top_k=top_k)
                        row["timings"]["bm25_query_seconds"] = time.perf_counter()-start
                        row["bm25_ranking"] = ranking
                        row["bm25_diagnostics"] = lexical.diagnostics
                        row["bm25"] = set_scores([d["symbol"] for d in ranking], stage["expected_impacts"])
                        row["outcome"] = "complete"
                    except (ValueError, OSError) as error:
                        row["outcome"] = "failed"
                        row["error"] = str(error)
                        for arm in ("graph", "direct", "bm25"):
                            row[arm] = set_scores([], stage["expected_impacts"], available=False)
                        row["graph_tests"] = set_scores([], stage["expected_tests"], available=False)
                        row["edges"] = set_scores([], [tuple(e) for e in stage["expected_edges"]], available=False)
                    if backend is not None and case["id"] in chosen:
                        if row["outcome"] != "complete" or model_stopped:
                            row["model"] = {"outcome": "not_run", "reason": "graph failure or prior model infrastructure failure", "scores": []}
                        else:
                            question = f"Which functions and tests might be affected by changing {case['target']}? Use supported paths and retain analysis gaps."
                            try:
                                trace = compare_continuation(store, backend, profile, case["target"], question,
                                    repeats=model_repeats, answer_tokens=answer_tokens)
                                scores = []
                                for run in trace["runs"]:
                                    if "answer" in run and "rendered" in run:
                                        score = score_response(run["rendered"], run["answer"]["generation"]["text"],
                                            stage["expected_impacts"], generation_complete=run["answer"]["outcome"] == "complete")
                                    else:
                                        score = {"gold": set_scores([], stage["expected_impacts"], available=False),
                                                 "accepted_gold": set_scores([], stage["expected_impacts"], available=False),
                                                 "task_complete": False, "support_precision": None,
                                                 "returned_claims": 0, "supported_claims": 0}
                                    scores.append({"arm": run["arm"], "trial": run["trial"], **score})
                                row["model"] = {"outcome": trace["outcome"], "trace": trace, "scores": scores}
                                if trace["outcome"] == "failed":
                                    model_stopped = True
                            except (ValueError, OSError) as error:
                                row["model"] = {"outcome": "failed", "error": str(error), "scores": []}
                                model_stopped = True
        tasks = result["tasks"]
        result["summary"] = {arm:aggregate([r[arm] for r in tasks]) for arm in ("bm25", "direct", "graph")}
        result["summary"]["graph_tests"] = aggregate([r["graph_tests"] for r in tasks])
        result["summary"]["edges"] = aggregate([r["edges"] for r in tasks])
        result["core_regression_pass"] = all(r["outcome"] == "complete" and r["graph"]["exact_match"]
            and r["edges"]["exact_match"] and r["validation"]["valid"] and r["fresh_rebuild_equal"]
            for r in tasks if r["category"] == "core")
        result["all_tasks_exact"] = all(r["graph"]["exact_match"] for r in tasks)
        result["known_gap_tasks"] = [{"case": r["case"], "stage": r["stage"], "graph": r["graph"]}
                                     for r in tasks if r["category"] == "known_gap"]
        if backend is not None:
            expected_runs = sum(2*model_repeats for r in tasks if r["case"] in chosen)
            scored = [s for r in tasks for s in r.get("model", {}).get("scores", [])]
            slots = []
            for row in tasks:
                if row["case"] not in chosen:
                    continue
                for trial in range(1, model_repeats+1):
                    for arm in ("upfront", "boundary"):
                        score = next((s for s in row.get("model", {}).get("scores", [])
                                      if s["arm"] == arm and s["trial"] == trial), None)
                        slots.append(score or {"arm": arm, "trial": trial, "task_complete": False,
                            "gold": set_scores([], row["gold"]["impacts"], available=False),
                            "accepted_gold": set_scores([], row["gold"]["impacts"], available=False),
                            "returned_claims": 0, "supported_claims": 0, "support_precision": None})
            result["model_summary"] = {"expected_runs": expected_runs, "recorded_runs": len(scored),
                "unrun_or_unscored_runs": expected_runs-len(scored),
                "task_complete_runs": sum(s["task_complete"] for s in scored),
                "task_complete_rate": sum(s["task_complete"] for s in scored)/expected_runs if expected_runs else None}
            for arm in ("upfront", "boundary"):
                selected = [s for s in slots if s["arm"] == arm]
                quality = aggregate([s["gold"] for s in selected])
                claims = sum(s["returned_claims"] for s in selected)
                result["model_summary"][arm] = {"gold": quality,
                    "accepted_gold": aggregate([s["accepted_gold"] for s in selected]),
                    "support_precision": sum(s["supported_claims"] for s in selected)/claims if claims else None}
        result["outcome"] = "complete" if all(r["outcome"] == "complete" for r in tasks) and not model_stopped else "failed"
        result["wall_seconds"] = time.perf_counter()-started
        return result
    finally:
        assert scratch.resolve().is_relative_to(scratch_root) and scratch.resolve() != scratch_root
        shutil.rmtree(scratch)
