"""Embedding-capable entity resolution proposals (read-only).

Production pattern (Neo4j builder, graphiti, Dedup literature):
blocking plus multi-signal scoring, deterministic matching first,
a stronger judge only for hard cases. Tunic's version: normalized-name
blocking, name plus structural signals, optional caller-supplied
vectors for cosine, pinned weights and threshold. Output is
`same_as` *proposals* with derivations -- never assertions, never
auto-merge. Promotion goes through the existing governed
review/promote path (`Memory.promote(..., rel="same_as")`), which
refuses duplicates and contradicts-blocked pairs.

Optimized for AI, not human reading: machine-checkable scores and
signal lists, no prose, no LLM call.
"""

import hashlib
import math

SAME_AS_RULE = "same_as_proposal_v1"
SAME_AS_THRESHOLD = 4

WEIGHT_EXACT_NORMALIZED = 3
WEIGHT_TOKEN_SUBSET = 1
WEIGHT_SAME_DIR = 1
WEIGHT_SHARED_NEIGHBOR = 1
WEIGHT_SHARED_NEIGHBOR_CAP = 2
WEIGHT_EMBEDDING = 2
EMBEDDING_COSINE_FLOOR = 0.85
WEIGHT_SHINGLE_JACCARD = 2
SHINGLE_JACCARD_FLOOR = 0.3

MINHASH_PERMS = 32
MINHASH_BANDS = 8
ENTROPY_FLOOR = 1.0
MIN_NORM_LEN = 3


def normalize(name):
    """Lowercase alphanumeric skeleton: GetUser, get_user and
    get-user all block together."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _bare(key):
    return key.split("::", 1)[-1]


def _directory(key):
    path = key.split("::", 1)[0]
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _tokens(name):
    import re
    return set(part for part in
               re.split(r"[^a-z0-9]+", name.lower()) if part)


def _shingles(text, width=3):
    """Character shingles over the normalized name (graphiti: 3-grams)."""
    if len(text) < width:
        return set()
    return {text[i:i + width] for i in range(len(text) - width + 1)}


def _minhash(shingles, perms=MINHASH_PERMS):
    """Deterministic MinHash signature: fixed seeds, no randomness.

    min over shingles of sha256("perm:shingle") per permutation row.
    """
    signature = []
    for row in range(perms):
        best = None
        for shingle in sorted(shingles):
            digest = hashlib.sha256(
                ("%d:%s" % (row, shingle)).encode("utf-8")).digest()
            value = int.from_bytes(digest[:8], "big")
            if best is None or value < best:
                best = value
        signature.append(best if best is not None else 0)
    return signature


def _lsh_blocks(signature, bands=MINHASH_BANDS):
    """Band keys: equal bands collide with probability ~ Jaccard."""
    rows = len(signature) // bands
    blocks = []
    for band in range(bands):
        chunk = tuple(signature[band * rows:(band + 1) * rows])
        blocks.append(("lsh", band, chunk))
    return blocks


def _entropy(text):
    """Shannon entropy in bits over characters."""
    if not text:
        return 0.0
    total = len(text)
    counts = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    return -sum((count / total) * math.log2(count / total)
                for count in counts.values())


def _specific(name):
    """Specificity gate: short or repetitive names defer to stronger
    evidence (exact match or structural corroboration), exactly as the
    entropy gate in graphiti's dedup defers to the LLM judge."""
    norm = normalize(name)
    return len(norm) >= MIN_NORM_LEN and _entropy(norm) >= ENTROPY_FLOOR


def _jaccard(left, right):
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(
        sum(b * b for b in right))
    if norm == 0.0:
        return 0.0
    return dot / norm


def _neighbors(view):
    """Undirected adjacency over static calls and asserted ties."""
    from .extract import TRAVERSABLE_CLAIMS
    adjacent = {}
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        status = assertion.get("status")
        if relation == "static_call":
            if status != "syntactic_observation":
                continue
        elif relation in TRAVERSABLE_CLAIMS:
            if status != "asserted_in_source":
                continue
        else:
            continue
        caller = proposition.get("caller")
        callee = proposition.get("callee")
        if caller and callee and caller != callee:
            adjacent.setdefault(caller, set()).add(callee)
            adjacent.setdefault(callee, set()).add(caller)
    return adjacent


def _decided_pairs(view):
    """Already-asserted same_as pairs and contradicts-blocked pairs."""
    done, blocked = set(), set()
    for assertion in view.get("assertions", []):
        if assertion.get("status") != "asserted_in_source":
            continue
        proposition = assertion.get("proposition", {})
        pair = frozenset((proposition.get("caller"),
                          proposition.get("callee")))
        if proposition.get("relation") == "same_as":
            done.add(pair)
        elif proposition.get("relation") == "contradicts":
            blocked.add(pair)
    return done, blocked


def propose_same_as(view, limit=10, vectors=None):
    """Propose same_as candidates between distinct entity keys.

    vectors maps key to embedding list when a caller has them
    (e.g. the pinned local cache); without vectors the name plus
    structural signals decide alone. Returns at most `limit`
    candidates sorted by (score desc, subject, object), each with
    its signals and total score. Read-only.
    """
    vectors = vectors or {}
    symbols = [entry.get("key") for entry in view.get("symbols", [])
               if entry.get("key") and not entry.get("key").endswith(
                   "::document")]
    blocks = {}
    for key in symbols:
        blocks.setdefault(("norm", normalize(_bare(key))), []).append(key)
        for token in _tokens(_bare(key)):
            blocks.setdefault(("tok", token), []).append(key)
        shingles = _shingles(normalize(_bare(key)))
        if shingles:
            for block in _lsh_blocks(_minhash(shingles)):
                blocks.setdefault(block, []).append(key)
    adjacent = _neighbors(view)
    done, blocked = _decided_pairs(view)
    candidates = []
    seen = set()
    for _kind, keys in blocks.items():
        unique = sorted(set(keys))
        if len(unique) < 2 or len(unique) > 200:
            continue
        for left, right in ((unique[i], unique[j])
                            for i in range(len(unique))
                            for j in range(i + 1, len(unique))):
            pair = frozenset((left, right))
            if pair in seen or pair in done or pair in blocked:
                continue
            seen.add(pair)
            signals = []
            score = 0
            if normalize(_bare(left)) == normalize(_bare(right)):
                signals.append("normalized_exact")
                score += WEIGHT_EXACT_NORMALIZED
            left_tokens, right_tokens = (_tokens(_bare(left)),
                                         _tokens(_bare(right)))
            if left_tokens and right_tokens and (
                    left_tokens <= right_tokens
                    or right_tokens <= left_tokens):
                signals.append("token_subset")
                score += WEIGHT_TOKEN_SUBSET
            if _directory(left) == _directory(right):
                signals.append("same_directory")
                score += WEIGHT_SAME_DIR
            shared = len(adjacent.get(left, set())
                          & adjacent.get(right, set()))
            if shared:
                capped = min(shared, WEIGHT_SHARED_NEIGHBOR_CAP)
                signals.append("shared_neighbors:%d" % capped)
                score += WEIGHT_SHARED_NEIGHBOR * capped
            if left in vectors and right in vectors:
                cosine = _cosine(vectors[left], vectors[right])
                if cosine >= EMBEDDING_COSINE_FLOOR:
                    signals.append("embedding_cosine:%.2f" % cosine)
                    score += WEIGHT_EMBEDDING
            left_shingles = _shingles(normalize(_bare(left)))
            right_shingles = _shingles(normalize(_bare(right)))
            if left_shingles and right_shingles:
                similarity = _jaccard(left_shingles, right_shingles)
                if similarity >= SHINGLE_JACCARD_FLOOR:
                    signals.append("shingle_jaccard:%.2f" % similarity)
                    score += WEIGHT_SHINGLE_JACCARD
            if "normalized_exact" not in signals \
                    and "token_subset" not in signals \
                    and not any(signal.startswith("shingle_jaccard")
                                for signal in signals):
                continue
            if "normalized_exact" not in signals and (
                    not _specific(_bare(left))
                    or not _specific(_bare(right))):
                continue
            if score >= SAME_AS_THRESHOLD:
                subject, object_ = sorted((left, right))
                candidates.append({
                    "subject": subject,
                    "object": object_,
                    "relation": "same_as",
                    "score": score,
                    "signals": signals,
                    "derivation": SAME_AS_RULE,
                })
    candidates.sort(key=lambda c: (-c["score"], c["subject"],
                                   c["object"]))
    return candidates[:limit] if type(limit) is int and limit >= 0 \
        else candidates
