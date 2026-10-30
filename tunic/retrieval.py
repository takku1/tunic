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
STAGE_WEIGHTS = {"exact": 2.0, "name": 1.0, "lexical": 0.5, "semantic": 0.5}


class EmbeddingProvider:
    """Pluggable semantic backend. The null provider disables the stage."""

    name = "none"

    def available(self):
        return False

    def rank(self, query, candidates, top_k):
        raise NotImplementedError("no embedding provider configured")


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

    def rank(self, query, candidates, top_k):
        import json
        import urllib.request
        vectors = {}
        for text in [query, *candidates]:
            body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
            request = urllib.request.Request(self.endpoint.rstrip("/") + "/api/embeddings",
                                             data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=120) as response:
                vectors[text] = json.loads(response.read().decode("utf-8"))["embedding"]
        query_vector = vectors.pop(query)
        scored = []
        for text in candidates:
            vector = vectors[text]
            dot = sum(a * b for a, b in zip(query_vector, vector))
            query_norm = sum(a * a for a in query_vector) ** 0.5
            text_norm = sum(b * b for b in vector) ** 0.5
            scored.append((dot / (query_norm * text_norm) if query_norm and text_norm else 0.0, text))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [text for _, text in scored[:max(top_k, 0)]]


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
