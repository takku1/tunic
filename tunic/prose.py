"""The document channel: claims, mentions, and sections from prose.

Runs after every code stage, against the merged symbol table. Prose
proposes and cites; it never proves a code relationship by itself.
"""

import json
import re
from pathlib import Path

from .identity import identity
from .relations import (CLAIM_RELATIONS, PROFILE, RETRACTABLE, _endpoint_kind,
                        check_relation_signature, is_iso_date)
from .structure import INVENTORY_DERIVATIONS

_CONSTANT_LEAF = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\Z")

def _build_docs(sources, symbols, assertions, diagnostics):
    """Populate document mentions and typed-claim assertions.

    Reads only text sources against an already built symbol table, so this
    phase can rerun alone when code bytes are unchanged. The mention dedup
    set restarts here exactly as in a full rebuild, which refresh replicates
    by always processing every text source.
    Mutates the passed-in containers; see extract for the full pipeline.
    """
    return _build_mentions(sources, symbols, assertions, diagnostics,
                             _build_claims(sources, symbols, assertions, diagnostics))


def _build_claims(sources, symbols, assertions, diagnostics, text_ids=None):
    """Populate typed-claim assertions from document claim blocks.

    Returns the per-source claim spans that hide claimed text from mention
    extraction. Order-independent: claims never deduplicate across sources.
    Pass the full-build text ids when running over a subset of sources so
    other documents' symbols are not mistaken for code.
    """
    if text_ids is None:
        text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items()
                    if record["source"] not in text_ids
                    and record.get("kind") != "class")

    def _resolve_reference(ref):
        if ref in symbols:
            return ("exact", [ref])
        matches = sorted(key for key in code_keys
                         if symbols[key]["name"] == ref or key.split("::", 1)[-1].split(".")[-1] == ref)
        if matches:
            return ("named", matches)
        return ("missing", [])

    claim_spans = {}
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        offset = 0
        lineno = 0
        in_fence = False
        fence_start = 0
        fence_line = 0
        body = []
        for raw_line in s["data"].splitlines(keepends=True):
            start, end = offset, offset + len(raw_line)
            offset = end
            lineno += 1
            try:
                stripped = raw_line.decode("utf-8").strip()
            except UnicodeError:
                stripped = ""
            if not in_fence and stripped == "```tunic-claims":
                in_fence = True
                fence_start = start
                fence_line = lineno
                body = []
                continue
            if in_fence:
                if stripped == "```":
                    in_fence = False
                    claim_spans.setdefault(s["id"], []).append((fence_start, end))
                    try:
                        block = b"".join(chunk for _, _, chunk in body).decode("utf-8")
                        claims = json.loads(block)
                        if not isinstance(claims, list):
                            raise ValueError("claim block must be a JSON list")
                    except (UnicodeError, ValueError) as error:
                        diagnostics.append({"path": s["path"], "line": fence_line,
                                            "kind": "malformed_claims", "error": str(error)})
                        continue
                    for claim in claims:
                        if (not isinstance(claim, dict) or not isinstance(claim.get("rel"), str)
                                or not isinstance(claim.get("subject"), str)
                                or not isinstance(claim.get("object"), str)):
                            diagnostics.append({"path": s["path"], "line": fence_line,
                                                "kind": "invalid_claim"})
                            continue
                        rel = claim["rel"]
                        if rel not in CLAIM_RELATIONS:
                            diagnostics.append({"path": s["path"], "line": fence_line,
                                                "kind": "unknown_relation", "relation": rel})
                            continue
                        retract_target = claim.get("retract")
                        if rel == "retracts":
                            if not isinstance(retract_target, str) or retract_target not in RETRACTABLE:
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "invalid_retract",
                                                    "expression": (claim.get("subject", "?") + " retracts "
                                                                   + claim["object"]),
                                                    "retract": retract_target})
                                continue
                        elif retract_target is not None:
                            diagnostics.append({"path": s["path"], "line": fence_line,
                                                "kind": "invalid_retract",
                                                "expression": (claim.get("subject", "?") + " " + rel + " "
                                                               + claim["object"]),
                                                "retract": retract_target})
                            continue
                        subject = claim["subject"]
                        s_kind, s_candidates = _resolve_reference(subject)
                        o_kind, o_candidates = _resolve_reference(claim["object"])
                        expression = subject + " " + rel + " " + claim["object"]
                        qualifiers = {}
                        if isinstance(claim.get("authority"), str):
                            qualifiers["authority"] = claim["authority"]
                        if "effective" in claim:
                            if is_iso_date(claim["effective"]):
                                qualifiers["effective"] = claim["effective"]
                            else:
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "invalid_effective", "expression": expression})
                        if s_kind == "exact" and o_kind == "exact":
                            if not check_relation_signature(rel, s_candidates[0], o_candidates[0]):
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "relation_signature",
                                                    "expression": expression})
                                continue
                            proposition = {"relation": rel, "caller": s_candidates[0], "callee": o_candidates[0]}
                            if rel == "retracts":
                                proposition["retract"] = retract_target
                            proposition.update(qualifiers)
                            if isinstance(claim.get("note"), str):
                                proposition["note"] = claim["note"]
                            assertion = {"proposition": proposition, "source": s["id"],
                                         "start": fence_start, "end": end,
                                         "resolution": {"expression": expression, "rule": "exact_key_match_v1",
                                                        "support": []},
                                         "producer": PROFILE, "status": "asserted_in_source"}
                            assertion["id"] = identity("assertion", assertion)
                            assertions.append(assertion)
                            continue
                        if (s_kind, o_kind) in (("exact", "named"), ("named", "exact")):
                            if s_kind == "exact":
                                fixed, candidates = s_candidates[0], o_candidates
                                subject_named = False
                            else:
                                fixed, candidates = o_candidates[0], s_candidates
                                subject_named = True
                            ambiguous = len(candidates) > 1
                            for candidate in candidates:
                                caller = candidate if subject_named else fixed
                                callee = fixed if subject_named else candidate
                                record = symbols[candidate]
                                support = [{"source": record["source"], "start": record["start"],
                                            "end": record["end"]}]
                                proposition = {"relation": rel, "caller": caller, "callee": callee,
                                               "candidate": candidate}
                                if rel == "retracts":
                                    proposition["retract"] = retract_target
                                proposition.update(qualifiers)
                                if isinstance(claim.get("note"), str):
                                    proposition["note"] = claim["note"]
                                assertion = {"proposition": proposition, "source": s["id"],
                                             "start": fence_start, "end": end,
                                             "resolution": {"expression": expression,
                                                            "rule": "name_match_proposed_v1",
                                                            "support": support},
                                             "producer": PROFILE,
                                             "status": "proposed_ambiguous" if ambiguous else "proposed"}
                                assertion["id"] = identity("assertion", assertion)
                                assertions.append(assertion)
                            if ambiguous:
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "ambiguous_reference", "expression": expression,
                                                    "candidates": sorted(candidates)})
                            continue
                        diagnostics.append({"path": s["path"], "line": fence_line,
                                            "kind": ("ambiguous_reference"
                                                     if s_kind == "named" or o_kind == "named"
                                                     else "unresolved_reference"),
                                            "expression": expression,
                                            "candidates": sorted(s_candidates + o_candidates)})
                else:
                    body.append((start, end, raw_line))
                continue
        if in_fence:
            diagnostics.append({"path": s["path"], "line": fence_line, "kind": "unclosed_claims"})
    return claim_spans


def _code_leaf_names(symbols, text_ids):
    """Mappable bare leaves over code symbols: {leaf: [keys]}."""
    leaf_names = {}
    for key, record in symbols.items():
        if record["source"] in text_ids or record.get("kind") == "class":
            continue
        leaf = record["name"].split(".")[-1]
        # The leaf of package.json::type is an English word and snake_case
        # keys repeat through data files, so neither is evidence about one
        # key. APP_NAME is a constant: prose naming it means it. All other
        # key-path entities are citable by exact key only.
        if _endpoint_kind(key) != "code" and not _CONSTANT_LEAF.match(leaf):
            continue
        if len(leaf) >= 2:
            leaf_names.setdefault(leaf, []).append(key)
    return leaf_names


def _exact_mention_refs(assertion_list):
    """(source id, callee) pairs of exact text mentions for corroboration."""
    return {(record.get("source"), record["proposition"].get("callee"))
            for record in assertion_list
            if record.get("proposition", {}).get("relation") == "text_mention"
            and record.get("resolution", {}).get("rule") == "exact_key_match_v1"}


def _slugify_heading(text):
    """GitHub-style slug for a heading line, never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def split_doc_sections(path, data):
    """Split a prose document into deterministic retrieval sections.

    Returns [(slug_or_None, start_byte, end_byte)]: byte-exact spans
    over the raw bytes, in document order. Markdown headings (ATX,
    outside fenced code blocks and YAML frontmatter) start sections;
    duplicate headings get -2/-3 suffixes; leading prose before the
    first heading is a slug-less preamble. Non-markdown prose (.txt,
    .rst) is one whole-document unit. Setext underlines, headings
    inside fenced code, and RST section adornments are NOT parsed:
    those spans stay in their enclosing section rather than risking
    false boundaries. Deterministic: same bytes always give the same
    sections, so citations stay stable across refreshes.
    """
    if Path(path).suffix.lower() != ".md":
        return [(None, 0, len(data))]
    lines = data.splitlines(keepends=True)
    offsets, heads = [0], []
    for raw in lines:
        offsets.append(offsets[-1] + len(raw))
    fence, frontmatter, fm_done = None, False, False
    index = 0
    if lines and lines[0].strip() == b"---":
        frontmatter = True
        index = 1
    for lineno in range(index, len(lines)):
        try:
            line = lines[lineno].decode("utf-8")
        except UnicodeError:
            continue
        stripped = line.strip()
        if frontmatter and not fm_done:
            if stripped in ("---", "..."):
                fm_done = True
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            if fence is None:
                fence = stripped[:3]
            elif fence == stripped[:3]:
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$", stripped)
        if match and match.group(2):
            heads.append((len(match.group(1)), match.group(2), offsets[lineno]))
    sections, counts = [], {}
    for pos, (level, title, start) in enumerate(heads):
        end = offsets[len(lines)]
        for deeper_level, _, deeper_start in heads[pos + 1:]:
            if deeper_level <= level:
                end = deeper_start
                break
        base = _slugify_heading(title)
        counts[base] = counts.get(base, 0) + 1
        slug = base if counts[base] == 1 else "%s-%d" % (base, counts[base])
        if end > start:
            sections.append((slug, start, end))
    if not heads:
        return [(None, 0, len(data))]
    if heads[0][2] > 0 and data[:heads[0][2]].strip():
        sections.insert(0, (None, 0, heads[0][2]))
    sections.sort(key=lambda section: section[1])
    return sections


def _build_mentions(sources, symbols, assertions, diagnostics, claim_spans, seen=None):
    """Populate document mention assertions in source order.

    Exact key matches across every document take priority over bare leaf
    matches anywhere: the exact pass runs to completion before the bare
    pass starts. Takes an optional seen set of (start, key) pairs so
    incremental refresh can seed mentions from unchanged documents and
    preserve full-rebuild first-wins dedup order. Returns the seen set.
    """
    seen_mentions = set() if seen is None else seen
    _build_exact_mentions(sources, symbols, assertions, claim_spans, seen_mentions)
    text_ids = {s["id"] for s in sources if s.get("text")}
    doc_freq = _leaf_document_frequency(
        [s for s in sources if s.get("text")],
        _code_leaf_names(symbols, text_ids))
    _build_bare_mentions(sources, symbols, assertions, diagnostics, claim_spans,
                         seen_mentions, text_ids,
                         doc_freq, _exact_mention_refs(assertions),
                         _claim_mention_refs(assertions))
    return seen_mentions


def _build_exact_mentions(sources, symbols, assertions, claim_spans, seen, text_ids=None):
    """Populate exact `path::symbol` mention assertions in source order."""
    if text_ids is None:
        text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items()
                    if record["source"] not in text_ids
                    and record.get("kind") != "class")
    seen_mentions = seen
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        hidden = claim_spans.get(s["id"], [])
        offset = 0
        for raw_line in s["data"].splitlines(keepends=True):
            start, end = offset, offset + len(raw_line)
            offset = end
            if any(first <= start < last for first, last in hidden):
                continue
            if b"::" not in raw_line:  # every key contains it
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeError:
                continue
            for key in code_keys:
                if key in line and (start, key) not in seen_mentions:
                    seen_mentions.add((start, key))
                    proposition = {"relation": "text_mention", "caller": mentioner, "callee": key}
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": key, "rule": "exact_key_match_v1", "support": []},
                                 "producer": PROFILE, "status": "syntactic_observation"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
    return seen_mentions


EVIDENCE_DF_THRESHOLD = 0.05


def _leaf_document_frequency(text_sources, leaf_names):
    """Fraction of text sources containing each bare leaf, one scan per doc.

    Corpus-relative, never a fixed stopword list: a leaf that appears
    everywhere carries no identity evidence for any single occurrence.
    Must run over every text source, not a subset, or frequencies skew.
    """
    pattern = re.compile("|".join(
        r"\b" + re.escape(leaf) + r"\b"
        for leaf in sorted(leaf_names, key=len, reverse=True))) if leaf_names else None
    counts = {leaf: 0 for leaf in leaf_names}
    total = 0
    for source in text_sources:
        try:
            text = source["data"].decode("utf-8")
        except UnicodeError:
            continue
        total += 1
        if pattern is None:
            continue
        for leaf in {match.group(0) for match in pattern.finditer(text)}:
            counts[leaf] += 1
    return {leaf: (counts[leaf] / total if total else 0.0) for leaf in leaf_names}


def _claim_mention_refs(assertion_list):
    """(source id, entity) pairs touched by asserted doc claims."""
    refs = set()
    for record in assertion_list:
        proposition = record.get("proposition", {})
        if (record.get("status") != "asserted_in_source"
                or proposition.get("relation") in ("static_call", "text_mention")):
            continue
        refs.add((record.get("source"), proposition.get("caller")))
        refs.add((record.get("source"), proposition.get("callee")))
    return refs


def _requalify(leaf, line, source_id, callee, doc_freq, exact_refs, claim_refs=frozenset(), inventory_keys=frozenset()):
    """Recompute admission for a kept mention under current corpus state.

    Lets incremental refresh reprocess documents whose bare mentions flip
    verdicts when frequencies or exact references change, so refresh output
    stays identical to a full rebuild without reprocessing everything.
    """
    frequency = doc_freq.get(leaf, 0.0)
    class_hit, module_hit, inventory = _context_hits(line, callee)
    signals = _mention_signals(line, leaf, (source_id, callee) in exact_refs, (source_id, callee) in claim_refs, class_hit, module_hit, inventory)
    if not signals and callee in inventory_keys:
        # Full rebuilds no longer file zero-signal inventory candidates
        # (see _build_bare_mentions); a kept one of that shape is stale,
        # so mark its document for reprocessing.
        return True
    return ("exact_same_document" in signals
            or "asserted_claim_same_document" in signals
            or "class_context" in signals
            or (frequency <= EVIDENCE_DF_THRESHOLD
                and ("dotted_or_call" in signals
                     or "module_path_context" in signals
                     or "inventory_list" in signals)))


INVENTORY_HEADER = re.compile(
    r"^\s*[-*]?\s*(defs?|classes?|functions?|methods?|members?|exports?|"
    r"symbols?|apis?|attributes?)\s*:", re.IGNORECASE)


PATH_TOKEN = re.compile(r"[\w$./-]+\.\w+")


def _mention_signals(line, leaf, exact_same_doc, claimed_same_doc,
                     class_hit=False, module_hit=False, inventory=False):
    """Independent corroborating signals for one bare occurrence.

    exact_same_document: the same document already references the entity
    exactly, so identity is established, not guessed. asserted_claim_same_
    document: the document asserts a typed claim touching the entity, which
    corroborates identity as strongly as an exact mention. class_context:
    the line names the candidate's own class next to the leaf, which is
    joint identity evidence as discriminating as an exact key. dotted_or_
    call: `recv.leaf` or `leaf(` syntax denotes the code entity, not a
    prose verb. module_path_context: the line names the candidate's file,
    corroborating identity by location. inventory_list: the leaf sits in
    an explicit member inventory (`defs:`, `classes:`, ...), which lists
    entities rather than using their names as prose. code_span: the leaf
    sits inside backticks. A backticked span alone is weak (prose backticks
    file names too) and never qualifies by itself.
    """
    signals = []
    if exact_same_doc:
        signals.append("exact_same_document")
    if claimed_same_doc:
        signals.append("asserted_claim_same_document")
    if class_hit:
        signals.append("class_context")
    if re.search(r"[\w$]+\." + re.escape(leaf) + r"\b|\b" + re.escape(leaf) + r"\s*\(",
                 line):
        signals.append("dotted_or_call")
    if module_hit:
        signals.append("module_path_context")
    if inventory:
        signals.append("inventory_list")
    if re.search(r"`[^`]*\b" + re.escape(leaf) + r"\b[^`]*`", line):
        signals.append("code_span")
    return signals


def _context_hits(line, callee_key):
    """Structural corroboration available to any bare occurrence.

    Returns (class_hit, module_hit, inventory): whether the line names the
    candidate's own class, names the candidate's file, or sits in an
    explicit member-inventory list. Derived from the key and the line
    alone, so full rebuilds and incremental refreshes agree exactly.
    """
    parts = callee_key.split("::")
    dotted = parts[1].split(".") if len(parts) > 1 else []
    class_hit = (len(dotted) > 1
                 and re.search(r"\b" + re.escape(dotted[-2]) + r"\b", line) is not None)
    cand_path = parts[0]
    module_hit = any(cand_path == token or cand_path.endswith("/" + token)
                     for token in PATH_TOKEN.findall(line))
    return class_hit, module_hit, INVENTORY_HEADER.match(line) is not None


def _build_bare_mentions(sources, symbols, assertions, diagnostics, claim_spans, seen,
                         text_ids=None, doc_freq=None, exact_refs=None,
                         claim_refs=None):
    """Populate bare leaf-name mention assertions in source order.

    Must run after the exact pass over every document: a bare match is
    suppressed when its (start, key) pair was already claimed by an exact
    match, even in a different document.

    Bare matches are qualified, not merely collected. A token match
    becomes a graph assertion with same-doc exact or claim evidence, with
    class context, or with dotted/call, file-path, or inventory-list
    syntax on a low-frequency leaf; anything weaker is recorded as a
    mention_candidate diagnostic. Backticked spans are recorded as a
    signal but never qualify alone: prose backticks tool names too.
    Candidates stay searchable through the lexical stage and visible for
    review, but never become evidence edges. doc_freq must cover every
    text source and exact_refs every exact mention, or subset refreshes
    diverge from full rebuilds.
    """
    if text_ids is None:
        text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items()
                    if record["source"] not in text_ids
                    and record.get("kind") != "class")
    seen_mentions = seen
    leaf_names = _code_leaf_names(symbols, text_ids)
    if doc_freq is None:
        doc_freq = _leaf_document_frequency(
            [s for s in sources if s.get("text")], leaf_names)
    if exact_refs is None:
        exact_refs = set()
    if claim_refs is None:
        claim_refs = set()
    leaf_pattern = None
    if leaf_names:
        leaf_pattern = re.compile("|".join(
            r"\b" + re.escape(leaf) + r"\b"
            for leaf in sorted(leaf_names, key=len, reverse=True)))
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        hidden = claim_spans.get(s["id"], [])
        offset = 0
        for lineno, raw_line in enumerate(s["data"].splitlines(keepends=True), start=1):
            start, end = offset, offset + len(raw_line)
            offset = end
            if any(first <= start < last for first, last in hidden):
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeError:
                continue
            exact_here = {key for key in code_keys if key in line}
            if leaf_pattern is None:
                continue
            present = {match.group(0) for match in leaf_pattern.finditer(line)}
            for leaf in sorted(present):
                candidates = leaf_names[leaf]
                for key in candidates:
                    if key in exact_here or (start, key) in seen_mentions:
                        continue
                    seen_mentions.add((start, key))
                    frequency = doc_freq.get(leaf, 0.0)
                    class_hit, module_hit, inventory = _context_hits(line, key)
                    signals = _mention_signals(
                        line, leaf, (s["id"], key) in exact_refs,
                        (s["id"], key) in claim_refs,
                        class_hit, module_hit, inventory)
                    qualified = ("exact_same_document" in signals
                                 or "asserted_claim_same_document" in signals
                                 or "class_context" in signals
                                 or (frequency <= EVIDENCE_DF_THRESHOLD
                                     and ("dotted_or_call" in signals
                                          or "module_path_context" in signals
                                          or "inventory_list" in signals)))
                    if not qualified:
                        if (not signals and symbols[key].get("derivation")
                                in INVENTORY_DERIVATIONS):
                            # A bare common word with no corroborating
                            # signal is not an investigation lead; filing
                            # it drowns review in noise. The leaf stays
                            # searchable through the lexical stage.
                            continue
                        diagnostics.append({"path": s["path"], "line": lineno,
                                            "kind": "mention_candidate",
                                            "expression": leaf, "callee": key,
                                            "candidate": key,
                                            "document_frequency": round(frequency, 4),
                                            "signals": signals})
                        continue
                    record = symbols[key]
                    support = [{"source": record["source"], "start": record["start"],
                                "end": record["end"]}]
                    proposition = {"relation": "text_mention", "caller": mentioner, "callee": key,
                                   "candidate": key}
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": leaf, "rule": "name_match_proposed_v1",
                                                "support": support,
                                                "qualification": {"document_frequency": round(frequency, 4),
                                                                  "signals": signals}},
                                 "producer": PROFILE,
                                 "status": "proposed_ambiguous" if len(candidates) > 1 else "proposed"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
    return seen_mentions
