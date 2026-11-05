"""Every tool response fits a byte budget, and says what it left out.

Tools build complete results; this sits at the MCP boundary and enforces
the bound in one place, so a new tool cannot reintroduce a megabyte
response. The cap is on the serialized bytes the caller receives, not on
edge counts, because a single edge can carry kilobytes of support.

A response within budget passes through unchanged. An oversized one is
reduced deterministically: small fields stay whole, large fields share
what remains, lists keep their order, and a `_truncated` receipt records
each cut with the arguments that fetch the rest. Results are recomputed
per call, so pin `snapshot` when paging across a refresh.
"""

import json

DEFAULT_MAX_BYTES = 32768
MIN_MAX_BYTES = 2048
MAX_MAX_BYTES = 1048576
RECEIPT_RESERVE = 1536
MAX_RECEIPTS = 8
# A list keeps at least this many items at reduced detail, rather than a
# few items in full: breadth is what tells a reader what else exists.
LIST_BREADTH = 16
MIN_ITEM_BYTES = 256


def serialized_size(value):
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True))


class _Cuts:
    def __init__(self):
        self.entries = []

    def record(self, path, total, kept, unit):
        self.entries.append({"path": path, "unit": unit, "total": total,
                             "returned": kept})


def _shrink_text(value, budget, path, cuts):
    keep = max(budget - 2, 0)
    while keep and serialized_size(value[:keep]) > budget:
        keep //= 2
    cuts.record(path, len(value), keep, "chars")
    return value[:keep]


def _shrink_list(value, budget, path, cuts):
    remaining = budget - 2
    cap = max(MIN_ITEM_BYTES, budget // LIST_BREADTH)
    kept = []
    for index, item in enumerate(value):
        allowance = min(cap, remaining - 2)
        # Leftover space never holds a fragment: a sliver counted as
        # returned would make paging skip the rest of that item.
        if allowance < 2 or (allowance < MIN_ITEM_BYTES
                             and serialized_size(item) > allowance):
            break
        part = _shrink(item, allowance, path + [index], cuts)
        kept.append(part)
        remaining -= serialized_size(part) + 2
    if len(kept) < len(value):
        cuts.record(path, len(value), len(kept), "items")
    return kept


def _shrink_dict(value, budget, path, cuts):
    """Water-fill: small children stay whole, large ones split the rest."""
    left = budget - 2 - sum(serialized_size(key) + 4 for key in value)
    ordered = sorted(value, key=lambda key: (serialized_size(value[key]), key))
    kept = {}
    for position, key in enumerate(ordered):
        share = max(left, 0) // (len(ordered) - position)
        kept[key] = _shrink(value[key], share, path + [key], cuts)
        left -= serialized_size(kept[key])
    return {key: kept[key] for key in value}


def _shrink(value, budget, path, cuts):
    if serialized_size(value) <= budget:
        return value
    if isinstance(value, str):
        return _shrink_text(value, budget, path, cuts)
    if isinstance(value, list):
        return _shrink_list(value, budget, path, cuts)
    if isinstance(value, dict):
        return _shrink_dict(value, budget, path, cuts)
    return value


def _receipt(full_size, cuts):
    """Largest cuts first; the receipt is itself bounded."""
    entries = sorted(cuts.entries, key=lambda e: (e["returned"] - e["total"], str(e["path"])))
    shown = []
    for entry in entries[:MAX_RECEIPTS]:
        shown.append(dict(entry, continue_path=entry["path"],
                          continue_offset=entry["returned"]))
        del shown[-1]["path"]
    return {"full_bytes": full_size, "cuts": len(entries), "shown": shown,
            "hint": "pass continue_path and continue_offset to read a cut field; "
                    "pass max_response_bytes to raise the limit"}


def bound(result, max_bytes=DEFAULT_MAX_BYTES):
    """The result if it fits, else a reduced copy with a `_truncated` receipt."""
    full_size = serialized_size(result)
    if full_size <= max_bytes:
        return result
    budget = max_bytes - RECEIPT_RESERVE
    while True:
        cuts = _Cuts()
        reduced = _shrink(result, budget, [], cuts)
        wrapped = dict(reduced) if isinstance(reduced, dict) else {"result": reduced}
        wrapped["_truncated"] = _receipt(full_size, cuts)
        wrapped["_truncated"]["returned_bytes"] = 0
        wrapped["_truncated"]["returned_bytes"] = serialized_size(wrapped)
        if wrapped["_truncated"]["returned_bytes"] <= max_bytes:
            return wrapped
        budget //= 2


def _node_at(result, path):
    node = result
    for segment in path:
        try:
            node = node[segment]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("continue_path does not exist: %r" % (path,)) from error
    return node


def page(result, path, offset, max_bytes=DEFAULT_MAX_BYTES):
    """The rest of one cut field, from offset, under the same bound."""
    if not isinstance(path, list) or not all(type(s) in (str, int) for s in path):
        raise ValueError("continue_path must be a list of keys and indexes")
    if type(offset) is not int or offset < 0:
        raise ValueError("continue_offset must be a non-negative integer")
    node = _node_at(result, path)
    if isinstance(node, (list, str)):
        window = node[offset:]
        total = len(node)
    else:
        window, total = node, None
    return bound({"continue_path": path, "continue_offset": offset, "total": total,
                  "value": window}, max_bytes)
