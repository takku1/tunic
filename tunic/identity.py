"""Canonical identities contain no machine-local paths or timestamps."""

import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(kind, value):
    return kind + ":" + digest(canonical(value).encode("utf-8"))
