"""Hybrid retrieval: staged candidates, weighted RRF fusion, budgets, abstention.

Stages propose candidates; only the graph compiler proves relationships.
Exact keys outrank bare names, which outrank lexical and semantic discovery:
fusion weights encode that priority (exact 2.0, name 1.0, lexical 0.5,
semantic 0.5) over reciprocal ranks, so a higher-priority stage always beats
a lower one on equal ranks and multi-stage candidates rank first. Ties break
by symbol key, so fusion is fully deterministic.

The semantic stage runs only when its provider reports available; otherwise
it is recorded as skipped and lexical remains the discovery floor. Budgets
cap each stage and the fused total. An empty fusion abstains.
"""

RRF_K = 60
STAGE_WEIGHTS = {"exact": 2.0, "name": 1.0, "lexical": 0.5, "semantic": 0.5,
                 "graph": 0.5}
MMR_LAMBDA_DEFAULT = 1.0


class EmbeddingProvider:
    """Pluggable semantic backend. The null provider disables the stage."""

    name = "none"

    def available(self):
        return False

    def embed_texts(self, texts):
        raise NotImplementedError("no embedding provider configured")

    def rank(self, query, candidates, top_k):
        """Cosine rank over embed_texts; identical for cached or live vectors."""
        vectors = self.embed_texts([query, *candidates])
        query_vector = vectors[0]
        query_norm = sum(a * a for a in query_vector) ** 0.5
        scored = []
        for text, vector in zip(candidates, vectors[1:]):
            dot = sum(a * b for a, b in zip(query_vector, vector))
            text_norm = sum(b * b for b in vector) ** 0.5
            score = (dot / (query_norm * text_norm)
                     if query_norm and text_norm else 0.0)
            scored.append((score, text))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [text for _, text in scored[:max(top_k, 0)]]


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local Ollama embeddings, used only when the model is installed.

    No download is ever attempted: when the named model is absent the
    provider reports unavailable and retrieval falls back to lexical.
    """

    def __init__(self, model="nomic-embed-text", endpoint="http://localhost:11434"):
        self.model = model
        self.endpoint = endpoint
        self.name = "ollama:" + model

    def available(self):
        from .ollama import Ollama
        try:
            models = Ollama(self.endpoint).models()
        except Exception:
            return False
        names = set()
        if isinstance(models, dict):
            for entry in models.get("models", []):
                if isinstance(entry, dict) and entry.get("name"):
                    names.add(entry["name"])
                elif isinstance(entry, str):
                    names.add(entry)
        elif isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict) and entry.get("name"):
                    names.add(entry["name"])
                elif isinstance(entry, str):
                    names.add(entry)
        return self.model in names or any(name.startswith(self.model + ":") for name in names)

    def embed_texts(self, texts):
        import json
        import urllib.request
        vectors = []
        for text in texts:
            body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
            request = urllib.request.Request(self.endpoint.rstrip("/") + "/api/embeddings",
                                             data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=120) as response:
                vectors.append(json.loads(response.read().decode("utf-8"))["embedding"])
        return vectors


def fuse(rankings, *, weights=None, total=None, threshold=0.0):
    """Weighted reciprocal-rank fusion over {stage: [keys in rank order]}.

    Returns [(score, key, {stage: rank})] sorted by score then key.
    Candidates below threshold are dropped; an empty fusion means abstain.
    """
    weights = dict(STAGE_WEIGHTS) if weights is None else dict(weights)
    for stage in rankings:
        if stage not in weights:
            raise ValueError("unknown retrieval stage: " + str(stage))
    if type(threshold) not in (int, float) or threshold < 0:
        raise ValueError("threshold must be a non-negative number")
    scores, origins = {}, {}
    for stage, keys in rankings.items():
        weight = weights[stage]
        for rank, key in enumerate(keys, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (RRF_K + rank)
            origins.setdefault(key, {})[stage] = rank
    fused = [(score, key, origins[key]) for key, score in scores.items()
             if score >= threshold]
    fused.sort(key=lambda row: (-row[0], row[1]))
    if type(total) is int and total >= 0:
        fused = fused[:total]
    return fused


def _tokens(text):
    import re
    return set(part for part in
               re.split(r"[^a-z0-9]+", text.lower()) if part)


def mmr_order(fused, texts, *, lambda_=MMR_LAMBDA_DEFAULT, limit=None):
    """Maximal-marginal-relevance reorder (Carbonell & Goldstein 1998).

    Pure, deterministic, model-free: relevance is the fused score,
    similarity is token Jaccard over caller-supplied candidate text
    (path plus symbol name suffices: same-file candidates penalize
    each other, which is exactly the crowding this removes).
    lambda_ 1.0 is the identity (pure relevance); 0.7 is the practical
    default when diversifying; 0.0 is maximal diversity. Ties break by
    (score, key) so output is reproducible by hand. limit caps the
    reselected prefix; None keeps every candidate.
    """
    if type(lambda_) not in (int, float) or not 0.0 <= lambda_ <= 1.0:
        raise ValueError("mmr lambda must be a number in [0, 1]")
    if not fused:
        return []
    if lambda_ == 1.0 and limit is None:
        return list(fused)
    token_sets = {key: _tokens(texts.get(key, key)) for _, key, _ in fused}
    remaining = list(fused)
    selected, selected_keys = [], []
    while remaining:
        best, best_value = None, None
        for score, key, origins in remaining:
            sims = [_jaccard(token_sets[key], token_sets[other])
                    for other in selected_keys]
            penalty = max(sims) if sims else 0.0
            value = lambda_ * score - (1.0 - lambda_) * penalty
            if (best_value is None or value > best_value
                    or (value == best_value
                        and (-score, key) < (-best[0], best[1]))):
                best, best_value = (score, key, origins), value
        selected.append(best)
        selected_keys.append(best[1])
        remaining = [row for row in remaining if row[1] != best[1]]
        if type(limit) is int and limit >= 0 and len(selected) >= limit:
            break
    return selected


def _jaccard(left, right):
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


PPR_ALPHA = 0.15
PPR_TOLERANCE = 1e-9
PPR_MAX_ITER = 1000


def ppr_scores(nodes, edges, subjects, alpha=PPR_ALPHA):
    """Exact personalized PageRank over a small directed graph.

    The FORA line (Wang et al., KDD 2017) proves approximation with
    guarantees is the right tool at web scale; below thousands of
    nodes exact power iteration is trivially affordable, so no
    approximation, no index, fully reproducible. Teleport vector is
    uniform over `subjects`; dangling mass redistributes through the
    teleport vector. Returns {node: score} summing to 1.0.
    Deterministic: sorted node order throughout.
    """
    ordered = sorted(set(nodes) | set(subjects))
    if not ordered:
        return {}
    index = {node: position for position, node in enumerate(ordered)}
    out = {node: [] for node in ordered}
    for source, target in edges:
        if source in index and target in index:
            out[source].append(target)
    seed = [1.0 / len(subjects) if node in set(subjects) else 0.0
            for node in ordered]
    if not any(seed):
        seed = [1.0 / len(ordered)] * len(ordered)
    rank = list(seed)
    size = len(ordered)
    for _ in range(PPR_MAX_ITER):
        dangling = sum(value for node, value in zip(ordered, rank)
                       if not out[node])
        nxt = [alpha * teleport for teleport in seed]
        share = (1.0 - alpha) * dangling
        for position in range(size):
            nxt[position] += share * seed[position]
        for position, node in enumerate(ordered):
            targets = out[node]
            if not targets:
                continue
            flow = (1.0 - alpha) * rank[position] / len(targets)
            for target in targets:
                nxt[index[target]] += flow
        delta = sum(abs(new - old) for new, old in zip(nxt, rank))
        rank = nxt
        if delta < PPR_TOLERANCE:
            break
    total = sum(rank)
    if total <= 0:
        return {node: 1.0 / size for node in ordered}
    return {node: rank[position] / total
            for position, node in enumerate(ordered)}
