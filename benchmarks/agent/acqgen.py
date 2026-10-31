"""ACQ v1 fixture generator: seeded, deterministic, frozen by (scenario, rep).

Ten scenarios x reps 0-4. Names, layouts, and question phrasings vary by
rep; dependency shapes are fixed per scenario. Layout alternates:
even reps use multi-file trees, odd reps a single shop.py. Question
phrasing cycles three variants. Regenerating with the same seeds yields
byte-identical fixtures; the generator file is the frozen fixture spec.

Scenario contract per build: files dict, question, gold triple or None,
apply checks, static-visibility expectation for the gold edge, and whether
an A3 registry candidate is expected in context.
"""
import hashlib

MODULES = ["billing", "pricing", "charging", "invoicing", "fare"]
TARGETS = ["round_total", "round_down", "quantize", "floor_price", "ceil_total"]
CALLERS = ["place_order", "checkout", "submit_cart", "confirm_buy", "finalize"]
DISPATCHERS = ["apply", "resolve", "route", "select", "delegate"]
TABLES = ["STRATEGIES", "HANDLERS", "MODES", "PLANS", "RULES"]
KEYS = ["standard", "default", "primary", "regular", "basic"]
QUESTIONS = ["Which functions break if {leaf} changes?",
             "What must change if {leaf} changes, and why?",
             "If {leaf} changes, what else is affected?"]

SCENARIOS = ("direct", "dict", "registry", "callback", "alias",
             "reassign", "dynkey", "contra", "correct", "irrelevant")


def names(rep):
    return {"module": MODULES[rep % 5], "target": TARGETS[rep % 5],
            "caller": CALLERS[rep % 5], "dispatch": DISPATCHERS[rep % 5],
            "table": TABLES[rep % 5], "key": KEYS[rep % 5]}


def question(rep, leaf):
    return QUESTIONS[rep % 3].format(leaf=leaf)


def key(path, symbol):
    return "%s::%s" % (path, symbol)


def single(path, body):
    return {path: body}


def build(scenario, rep):
    n = names(rep)
    multi = (rep % 2 == 0)
    ask = lambda leaf: question(rep, leaf)
    if scenario == "direct":
        target, caller = n["target"], n["caller"]
        if multi:
            files = {"target.py": "def %s(cart):\n    return cart\n" % target,
                     "buyer.py": "from target import %s\n\n\ndef %s(cart):\n    return %s(cart)\n"
                     % (target, caller, target)}
            gold = ("depends_on", key("buyer.py", caller), key("target.py", target))
        else:
            files = {"shop.py": "def %s(cart):\n    return cart\n\n\ndef %s(cart):\n    return %s(cart)\n"
                     % (target, caller, target)}
            gold = ("depends_on", key("shop.py", caller), key("shop.py", target))
        return {"files": files, "question": ask(target), "gold": gold,
                "static": "visible",
                "apply": {"question": ask(target), "gold": [gold[1]], "obsolete": []},
                "a3_expected": False, "kind": "positive"}
    if scenario == "dict":
        target = n["target"]
        if multi:
            files = {"reg.py": ("from target import %s\n\nTABLE = {\"lit\": %s}\n\n\n"
                                 "def run(x):\n    return TABLE[\"lit\"](x)\n" % (target, target)),
                     "target.py": "def %s(cart):\n    return cart\n" % target}
            gold = ("depends_on", key("reg.py", "run"), key("target.py", target))
        else:
            files = {"shop.py": ("def %s(cart):\n    return cart\n\nTABLE = {\"lit\": %s}\n\n\n"
                                 "def run(x):\n    return TABLE[\"lit\"](x)\n" % (target, target))}
            gold = ("depends_on", key("shop.py", "run"), key("shop.py", target))
        return {"files": files, "question": ask(target), "gold": gold,
                "static": "invisible",
                "apply": {"question": ask(target), "gold": [gold[1]], "obsolete": []},
                "a3_expected": True, "kind": "positive"}
    if scenario == "registry":
        target, caller = n["target"], n["caller"]
        table, disp, modkey = n["table"], n["dispatch"], n["key"]
        if multi:
            files = {"reg.py": ("from target import %s\n\n%s = {\"%s\": %s}\n\n\n"
                                "def %s(how, x):\n    return %s[how](x)\n"
                                % (target, table, modkey, target, disp, table)),
                     "target.py": "def %s(cart):\n    return cart\n" % target,
                     "buyer.py": ("from reg import %s\n\n\ndef %s(cart):\n"
                                  "    return %s(\"%s\", cart)\n"
                                  % (disp, caller, disp, modkey))}
            gold = ("depends_on", key("buyer.py", caller), key("target.py", target))
        else:
            files = {"shop.py": ("def %s(cart):\n    return cart\n\n%s = {\"%s\": %s}\n\n\n"
                                 "def %s(how, x):\n    return %s[how](x)\n\n\n"
                                 "def %s(cart):\n    return %s(\"%s\", cart)\n"
                                 % (target, table, modkey, target, disp, table,
                                    caller, disp, modkey))}
            gold = ("depends_on", key("shop.py", caller), key("shop.py", target))
        return {"files": files, "question": ask(target), "gold": gold,
                "static": "invisible",
                "apply": {"question": ask(target), "gold": [gold[1]], "obsolete": []},
                "a3_expected": True, "kind": "positive"}
    if scenario == "callback":
        if multi:
            files = {"ops.py": ("def process(x):\n    return x\n\n\n"
                                "def run(fn, x):\n    return fn(x)\n"),
                     "main.py": ("from ops import run, process\n\n\ndef entry(x):\n"
                                 "    return run(process, x)\n")}
            gold = ("depends_on", key("main.py", "entry"), key("ops.py", "process"))
        else:
            files = {"shop.py": ("def process(x):\n    return x\n\n\n"
                                 "def run(fn, x):\n    return fn(x)\n\n\n"
                                 "def entry(x):\n    return run(process, x)\n")}
            gold = ("depends_on", key("shop.py", "entry"), key("shop.py", "process"))
        return {"files": files, "question": ask("process"), "gold": gold,
                "static": "invisible",
                "apply": {"question": ask("process"), "gold": [gold[1]], "obsolete": []},
                "a3_expected": False, "kind": "positive"}
    if scenario == "alias":
        # Always multi-file: a same-file alias assignment is a different
        # (module-binding) pattern, not an import alias.
        target, caller = n["target"], n["caller"]
        files = {"target.py": "def %s(cart):\n    return cart\n" % target,
                 "buyer.py": ("from target import %s as t\n\n\ndef %s(cart):\n"
                              "    return t(cart)\n" % (target, caller))}
        gold = ("depends_on", key("buyer.py", caller), key("target.py", target))
        return {"files": files, "question": ask(target), "gold": gold,
                "static": "visible",
                "apply": {"question": ask(target), "gold": [gold[1]], "obsolete": []},
                "a3_expected": False, "kind": "positive"}
    if scenario in ("reassign", "dynkey"):
        target = n["target"]
        table = n["table"]
        if multi:
            files = {"reg.py": ("from target import %s\n\n%s = {\"lit\": %s}\n\n\n"
                                "def run(how, x):\n    return %s[how](x)\n"
                                % (target, table, target, table)),
                     "target.py": "def %s(cart):\n    return cart\n" % target,
                     "patch.py": ("from reg import %s\nfrom target import %s\n\n"
                                  "%s[\"late\"] = %s\n" % (table, target, table, target))
                     if scenario == "reassign" else
                     ("from reg import %s\n\n\ndef flex(name, x):\n"
                      "    return %s[name](x)\n" % (table, table))}
        else:
            files = {"shop.py": ("def %s(cart):\n    return cart\n\n%s = {\"lit\": %s}\n\n\n"
                                 "def run(how, x):\n    return %s[how](x)\n\n%s[\"late\"] = %s\n"
                                 % (target, table, target, table, table, target))
                     if scenario == "reassign" else
                     ("def %s(cart):\n    return cart\n\n%s = {\"lit\": %s}\n\n\n"
                      "def flex(name, x):\n    return %s[name](x)\n"
                      % (target, table, target, table))}
        return {"files": files, "question": ask(target), "gold": None,
                "static": "na", "apply": None, "a3_expected": False, "kind": "abstain"}
    if scenario == "contra":
        cell = build("registry", rep)
        cell["kind"] = "contra"
        return cell
    if scenario == "correct":
        target = n["target"]
        caller = n["caller"]
        if multi:
            files = {"t1.py": "def old_fn(cart):\n    return cart\n",
                     "t2.py": "def %s(cart):\n    return cart\n" % target,
                     "buyer.py": "from t2 import %s\n\n\ndef %s(cart):\n    return %s(cart)\n"
                     % (target, caller, target)}
            e1 = ("depends_on", key("buyer.py", caller), key("t1.py", "old_fn"))
            e2 = ("depends_on", key("buyer.py", caller), key("t2.py", target))
        else:
            files = {"shop.py": ("def old_fn(cart):\n    return cart\n\n"
                                 "def %s(cart):\n    return cart\n\n\n"
                                 "def %s(cart):\n    return %s(cart)\n"
                                 % (target, caller, target))}
            e1 = ("depends_on", key("shop.py", caller), key("shop.py", "old_fn"))
            e2 = ("depends_on", key("shop.py", caller), key("shop.py", target))
        return {"files": files, "question": ask(target), "gold": e2, "stale": e1,
                "static": "visible",
                "apply": [{"question": ask(target), "gold": [e2[1]], "obsolete": [e1[1]]}],
                "a3_expected": False, "kind": "correct"}
    if scenario == "irrelevant":
        target = n["target"]
        files = {"shop.py": "def %s(cart):\n    return cart\n" % target}
        return {"files": files, "question": "Which functions break if frobnicate changes?",
                "gold": None, "static": "na", "apply": None,
                "a3_expected": False, "kind": "abstain"}
    raise ValueError("unknown scenario: " + scenario)


def digest(scenario, rep):
    cell = build(scenario, rep)
    blob = repr(sorted(cell["files"].items())) + cell["question"] + repr(cell["gold"])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
