"""Cross-session memory-reuse fixture (dev set; NOT the frozen suite).

The static graph covers every static edge. Session-A governed
acquisitions (reviewer notes + proof-backed frontier resolution) add
exactly the knowledge Session B measures:

- ``supersedes(handle_new, handle_old)``: correction (retirement).
- ``depends_on(dispatch, do_legacy)``: audit-verified dynamic routing.
- ``contradicts(route_b, route)``: disputed practice (bypasses migration).
"""


def handle_old(request):
    return "old:" + request


def handle_new(request):
    return "new:" + request


def route(request):
    return handle_new(request)


def route_b(request):
    return handle_old(request)


def do_legacy(kind, item):
    return (kind, item)


def do_new(kind, item):
    return ("new", kind, item)


TABLE = {"legacy": do_legacy, "new": do_new}


def dispatch(kind, item):
    return TABLE[kind](item)


def main():
    return dispatch("legacy", 1)
