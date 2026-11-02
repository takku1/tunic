"""Cross-session lifecycle device fixture (dev set; NOT the frozen suite).

Static graph only. Lifecycle sessions add reviewer audit notes with
redundant corroboration (the same dependency asserted twice), a
retirement correction, and an unrelated edge for negative controls.
"""


def old_handler(request):
    return "old:" + request


def new_handler(request):
    return "new:" + request


def route(request):
    return new_handler(request)


def legacy_worker(kind, item):
    return (kind, item)


TABLE = {"legacy": legacy_worker}


def dispatch(kind, item):
    return TABLE[kind](item)


def entry():
    return dispatch("legacy", 1)
