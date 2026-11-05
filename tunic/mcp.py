"""Stable agent-memory boundary: MCP-compatible JSON-RPC 2.0 over stdio.

Thin adapter only. Every tool delegates to Memory or the compiler; no graph
logic lives here. The workspace root is fixed at server start and every
agent-controlled path is confined inside it by Memory, so a connected agent
can read evidence, record notes, and propose memories but cannot redirect
writes or re-root the store. Tool arguments are validated before dispatch;
failures return structured errors, never tracebacks.
"""

from . import bounded
import json
import sys

from .compiler import (POLICIES, compile_callees, compile_impact,
                       compile_related, compile_review, compile_timeline)
from .extract import CLAIM_RELATIONS
from .memory import Memory

SERVER_VERSION = "0.1.0"


def _fail(message):
    raise ValueError(message)


def _check(value, kinds, name, empty_ok=False):
    if isinstance(value, bool) or not isinstance(value, kinds):
        names = kinds.__name__ if isinstance(kinds, type) else " or ".join(k.__name__ for k in kinds)
        _fail("tool argument %r must be %s" % (name, names))
    if not empty_ok and isinstance(value, str) and not value:
        _fail("tool argument %r must be non-empty" % (name,))
    return value


def _need(args, name, kind):
    return _check(args.get(name), kind, name)


def _optional(args, name, kind, default=None):
    if name not in args or args[name] is None:
        return default
    return _check(args[name], kind, name, empty_ok=True)


def _policy(args):
    policy = args.get("policy", "transparent")
    if policy not in POLICIES:
        _fail("unknown policy: %r (expected one of %s)" % (policy, ", ".join(POLICIES)))
    return policy


_BOUND_PROPERTIES = {
    "max_response_bytes": {"type": "integer",
                           "description": "response size limit; default %d"
                                          % bounded.DEFAULT_MAX_BYTES},
    "continue_path": {"type": "array",
                      "description": "from a _truncated receipt: the field to read on"},
    "continue_offset": {"type": "integer",
                        "description": "from a _truncated receipt: where to resume"}}


def tool_schemas():
    tools = _tool_schemas()
    for tool in tools:
        tool["inputSchema"].setdefault("properties", {}).update(_BOUND_PROPERTIES)
    return tools


def _tool_schemas():
    """Tool names with JSON-schema arguments; the tools/list payload."""
    return [
        {"name": "context",
         "description": "Start here. One bounded evidence pack for a task: each relevant node with its code, proven calls, unverified name references, claims, open questions, plus doc passages and stated gaps. The pack's trust field says how far to rely on each kind of link.",
         "inputSchema": {"type": "object",
                         "required": ["task"],
                         "properties": {
                             "task": {"type": "string"},
                             "limit": {"type": "integer"},
                             "max_bytes": {"type": "integer"},
                             "snapshot": {"type": "string"}}}},
        {"name": "retrieve",
         "description": "Audit view of retrieval: every stage, score, and selection decision behind an answer. Prefer context unless you need that trace. "
                        "Answer a natural-language task with fused graph evidence. "
                        "Optional select runs the evidence-aware selection layer; "
                        "container_limit bounds method-to-class expansion.",
         "inputSchema": {"type": "object",
                         "required": ["task"],
                         "properties": {
                             "task": {"type": "string"},
                             "limit": {"type": "integer"},
                             "policy": {"type": "string"},
                             "threshold": {"type": "number"},
                             "select": {"type": "boolean"},
                             "snapshot": {"type": "string"},
                             "container_limit": {"type": "integer"},
                        "exclude": {"type": "array",
                                    "items": {"type": "string"}}}}},
        {"name": "related",
         "description": "Cross-source context for one exact path::symbol key.",
         "inputSchema": {"type": "object",
                         "required": ["symbol"],
                         "properties": {
                             "symbol": {"type": "string"},
                             "policy": {"type": "string"}}}},
        {"name": "impact",
         "description": "Possible reverse-call impact for one exact path::symbol key.",
         "inputSchema": {"type": "object",
                         "required": ["symbol"],
                         "properties": {
                             "symbol": {"type": "string"},
                             "policy": {"type": "string"},
                             "max_depth": {"type": "integer"},
                             "max_nodes": {"type": "integer"}}}},
        {"name": "references",
         "description": "Textual identifier occurrences in non-parsed source "
                        "files (e.g. .astro): investigation leads, NOT "
                        "verified call edges. Use related/callees for proof.",
         "inputSchema": {"type": "object",
                         "required": ["symbol"],
                         "properties": {
                             "symbol": {"type": "string"},
                             "extensions": {"type": "array",
                                            "items": {"type": "string"}},
                             "limit": {"type": "integer"},
                             "max_bytes": {"type": "integer"},
                             "max_files": {"type": "integer"}}}},
        {"name": "callees",
         "description": "Forward-call traversal for one exact path::symbol key: direct, bounded reachable, and candidate callees plus cold receipts.",
         "inputSchema": {"type": "object",
                         "required": ["symbol"],
                         "properties": {
                             "symbol": {"type": "string"},
                             "policy": {"type": "string"},
                             "max_depth": {"type": "integer"},
                             "max_nodes": {"type": "integer"}}}},
        {"name": "timeline",
         "description": "Valid-time evidence view for one exact path::symbol key.",
         "inputSchema": {"type": "object",
                         "required": ["symbol"],
                         "properties": {
                             "symbol": {"type": "string"},
                             "at": {"type": "string"}}}},
        {"name": "review",
         "description": "Read-only structured acquisition report for one document.",
         "inputSchema": {"type": "object",
                         "required": ["document"],
                         "properties": {"document": {"type": "string"}}}},
        {"name": "note",
         "description": "Append a timestamped session note inside the workspace.",
         "inputSchema": {"type": "object",
                         "required": ["text"],
                         "properties": {
                             "text": {"type": "string"},
                             "document": {"type": "string"}}}},
        {"name": "candidates",
         "description": "List live proposed-but-unverified relationship candidates for review.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "acquire",
         "description": "Commit one reviewed candidate through the governed path. "
                        "The candidate stays proposed until reviewed; selection records "
                        "a justificationed receipt. Publishes before returning.",
         "inputSchema": {"type": "object",
                         "required": ["candidate_id", "justification"],
                         "properties": {
                             "candidate_id": {"type": "string"},
                             "justification": {"type": "string"},
                             "task": {"type": "string"}}}},
        {"name": "correct",
         "description": "Record a correction, retraction, or supersession and publish it. "
                        "Use contradicts to contest, retracts (with retract naming the "
                        "relation) to withdraw, supersedes to replace.",
         "inputSchema": {"type": "object",
                         "required": ["subject", "object", "rel"],
                         "properties": {
                             "subject": {"type": "string"},
                             "object": {"type": "string"},
                             "rel": {"type": "string"},
                             "retract": {"type": "string"},
                             "document": {"type": "string"},
                             "authority": {"type": "string"},
                             "note": {"type": "string"}}}},
        {"name": "frontier",
         "description": "List unresolved-knowledge ledger entries (gaps the agent could close). "
                        "Raw ledger rows: use limit for a bounded sample; triage-style "
                        "ranking is a later milestone, not this tool.",
         "inputSchema": {"type": "object",
                         "properties": {
                             "status": {"type": "string"},
                             "limit": {"type": "integer"}}}},
        {"name": "promote",
         "description": "Explicitly approve one candidate relationship as a claim.",
         "inputSchema": {"type": "object",
                         "required": ["subject", "object"],
                         "properties": {
                             "subject": {"type": "string"},
                             "object": {"type": "string"},
                             "rel": {"type": "string"},
                             "effective": {"type": "string"},
                             "authority": {"type": "string"},
                             "document": {"type": "string"},
                             "note": {"type": "string"}}}},
        {"name": "refresh",
         "description": "Incrementally re-ingest the fixed workspace root.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "coverage",
         "description": "Report what each in-scope file can establish (edges, entities, symbols, text), what was left out and why, and which grammars are missing. Check this before reading an empty answer as absence.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


def _bounded_call(memory, name, args):
    """handle_call under the response bound; see tunic/bounded.py."""
    if not isinstance(args, dict):
        return handle_call(memory, name, args)
    args = dict(args)
    limit = args.pop("max_response_bytes", bounded.DEFAULT_MAX_BYTES)
    if (type(limit) is not int
            or not bounded.MIN_MAX_BYTES <= limit <= bounded.MAX_MAX_BYTES):
        _fail("max_response_bytes must be an integer from %d to %d"
              % (bounded.MIN_MAX_BYTES, bounded.MAX_MAX_BYTES))
    path = args.pop("continue_path", None)
    offset = args.pop("continue_offset", 0)
    result = handle_call(memory, name, args)
    if path is not None:
        return bounded.page(result, path, offset, limit)
    return bounded.bound(result, limit)


def handle_call(memory, name, args):
    """Dispatch one validated tool call; raises ValueError on bad arguments.

    Callers may pass an envelope-level "principal" argument. It defaults
    to the server's own principal; anything else is denied loudly by
    tunic.memory.authorize before any tool logic runs.
    """
    if not isinstance(args, dict):
        _fail("tool arguments must be an object")
    from .memory import authorize
    authorize(args.get("principal", memory.principal), name)
    if name == "context":
        from . import pack
        limit = _optional(args, "limit", int, 5)
        if limit < 0:
            _fail("tool argument 'limit' must be a non-negative integer")
        return pack.build(memory, _need(args, "task", str), limit=limit,
                          max_bytes=_optional(args, "max_bytes", int, 12000),
                          snapshot=_optional(args, "snapshot", str))
    if name == "retrieve":
        task = _need(args, "task", str)
        limit = _optional(args, "limit", int, 5)
        if limit < 0:
            _fail("tool argument 'limit' must be a non-negative integer")
        threshold = _optional(args, "threshold", (int, float), 0.0)
        if threshold < 0:
            _fail("tool argument 'threshold' must be a non-negative number")
        select = args.get("select", False)
        if not isinstance(select, bool):
            _fail("tool argument 'select' must be bool")
        snapshot = _optional(args, "snapshot", str)
        container_limit = _optional(args, "container_limit", int, 2)
        if container_limit < 0:
            _fail("tool argument 'container_limit' must be a non-negative integer")
        exclude = args.get("exclude", [])
        if (not isinstance(exclude, list)
                or not all(isinstance(item, str) for item in exclude)):
            _fail("tool argument 'exclude' must be a list of strings")
        budget = args.get("budget", {})
        if (not isinstance(budget, dict)
                or not all(isinstance(key, str) for key in budget)):
            _fail("tool argument 'budget' must be an object")
        mmr_lambda = args.get("mmr_lambda", 1.0)
        if (not isinstance(mmr_lambda, (int, float))
                or not 0.0 <= mmr_lambda <= 1.0):
            _fail("tool argument 'mmr_lambda' must be a number in [0, 1]")
        return memory.answer(task, limit=limit, policy=_policy(args),
                             threshold=threshold, select=select,
                             snapshot=snapshot, exclude=exclude,
                             container_limit=container_limit,
                             budget=budget, mmr_lambda=mmr_lambda)
    if name == "related":
        return compile_related(memory.store, _need(args, "symbol", str),
                               policy=_policy(args))
    if name == "impact":
        depth = _optional(args, "max_depth", int, 8)
        nodes = _optional(args, "max_nodes", int, 100)
        if depth < 1 or nodes < 1:
            _fail("max_depth and max_nodes must be positive integers")
        return compile_impact(memory.store, _need(args, "symbol", str),
                              policy=_policy(args),
                              max_depth=depth, max_nodes=nodes)
    if name == "callees":
        depth = _optional(args, "max_depth", int, 8)
        nodes = _optional(args, "max_nodes", int, 100)
        if depth < 1 or nodes < 1:
            _fail("max_depth and max_nodes must be positive integers")
        return compile_callees(memory.store, _need(args, "symbol", str),
                               policy=_policy(args),
                               max_depth=depth, max_nodes=nodes)
    if name == "references":
        symbol = _need(args, "symbol", str)
        kwargs = {}
        if args.get("extensions") is not None:
            exts = args.get("extensions")
            if (not isinstance(exts, list)
                    or not all(isinstance(item, str) for item in exts)):
                _fail("tool argument 'extensions' must be a list of strings")
            kwargs["extensions"] = exts
        kwargs["limit"] = _optional(args, "limit", int, 20)
        for field in ("max_bytes", "max_files"):
            value = _optional(args, field, int)
            if value is not None:
                kwargs[field] = value
        return memory.references(symbol, **kwargs)
    if name == "timeline":
        return compile_timeline(memory.store, _need(args, "symbol", str),
                                at=_optional(args, "at", str))
    if name == "review":
        return compile_review(memory.store, _need(args, "document", str))
    if name == "note":
        return {"outcome": "appended",
                "path": memory.note(_need(args, "text", str),
                                    _optional(args, "document", str,
                                              "notes/session.md"))}
    if name == "promote":
        rel = _optional(args, "rel", str, "depends_on")
        if rel not in CLAIM_RELATIONS:
            _fail("unknown relation: %r" % (rel,))
        kwargs = {"rel": rel}
        for field in ("effective", "authority", "document", "note"):
            value = _optional(args, field, str)
            if value is not None:
                kwargs[field] = value
        return {"outcome": "recorded",
                "path": memory.promote(_need(args, "subject", str),
                                       _need(args, "object", str), **kwargs)}
    if name == "refresh":
        return memory.refresh()
    if name == "coverage":
        from . import coverage
        return coverage.report(memory.root)
    if name == "candidates":
        if args:
            _fail("candidates takes no arguments")
        return memory.candidates()
    if name == "acquire":
        receipt = memory.commit_candidate(
            _need(args, "candidate_id", str),
            _need(args, "justification", str),
            task=_optional(args, "task", str))
        published = memory.ingest()
        receipt["snapshot"] = published.get("snapshot")
        return receipt
    if name == "correct":
        rel = _need(args, "rel", str)
        if rel not in ("contradicts", "retracts", "supersedes"):
            _fail("unknown correction relation: %r "
                  "(expected contradicts, retracts, or supersedes)" % (rel,))
        kwargs = {}
        for field in ("document", "authority", "note", "retract"):
            value = _optional(args, field, str)
            if value is not None:
                kwargs[field] = value
        if rel == "retracts" and "retract" not in kwargs:
            _fail("retracts requires the retracted relation in 'retract'")
        memory.record_outcome(_need(args, "subject", str),
                              _need(args, "object", str), rel=rel, **kwargs)
        published = memory.ingest()
        return {"outcome": "recorded", "rel": rel,
                "snapshot": published.get("snapshot")}
    if name == "frontier":
        status = _optional(args, "status", str)
        if status is not None and status not in ("open", "investigating",
                                                "resolved", "dismissed"):
            _fail("unknown frontier status: %r" % (status,))
        limit = _optional(args, "limit", int)
        if limit is not None and limit < 0:
            _fail("tool argument 'limit' must be a non-negative integer")
        entries = memory.frontier_list(status=status)
        return {"status": status or "all", "total": len(entries),
                "entries": entries if limit is None else entries[:limit]}
    _fail("unknown tool: %s" % (name,))


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def handle_request(memory, message):
    """Handle one JSON-RPC message; returns None for notifications."""
    if not isinstance(message, dict):
        return _error(None, -32600, "request must be an object")
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})
    if not isinstance(method, str):
        return _error(request_id, -32600, "request method must be a string")
    if not isinstance(params, dict):
        return _error(request_id, -32602, "request params must be an object")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"protocolVersion": "2024-11-05",
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "tunic", "version": SERVER_VERSION}}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"tools": tool_schemas()}}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(request_id, -32602, "tool name must be a string")
        try:
            result = _bounded_call(memory, name, params.get("arguments", {}))
        except PermissionError as error:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text",
                                            "text": json.dumps({"error": str(error)},
                                                               ensure_ascii=True,
                                                               sort_keys=True)}],
                               "isError": True}}
        except (ValueError, KeyError, TypeError, OSError) as error:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text",
                                            "text": json.dumps({"error": str(error)},
                                                               ensure_ascii=True,
                                                               sort_keys=True)}],
                               "isError": True}}
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(result, ensure_ascii=True,
                                                           sort_keys=True)}],
                           "isError": False}}
    if method.startswith("notifications/"):
        return None
    return _error(request_id, -32601, "unknown method: %s" % (method,))


def serve(db, root, stdin=None, stdout=None):
    """Serve newline-delimited JSON-RPC on stdio until EOF."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    memory = Memory(db, root)
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                stdout.write(json.dumps(_error(None, -32700, "invalid JSON"),
                                        ensure_ascii=True, sort_keys=True) + "\n")
                stdout.flush()
                continue
            try:
                response = handle_request(memory, message)
            except (ValueError, KeyError, TypeError, OSError) as error:
                response = _error(message.get("id") if isinstance(message, dict) else None,
                                  -32603, str(error))
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=True, sort_keys=True) + "\n")
                stdout.flush()
    finally:
        memory.close()
    return 0
