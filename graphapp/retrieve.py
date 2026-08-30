"""Turning a plain-language question into the failure it is about.

Semantic first, lexical as fallback — a cascade, not a blend. Rank fusion was
tried and measured *worse* than either signal alone: a vector index always
returns k neighbours however irrelevant, so it answers confidently when the
question is out of scope, while BM25 on a short question against long documents
is dominated by common words. Blending let each one's noise outvote the other's
correct answer.

So semantic decides when it is confident — a question describes a symptom and a
Condition describes a symptom, the most direct comparison available — and
lexical speaks only when semantic abstains, which is exactly where exact
platform terminology ("ImagePullBackOff", "lock id") lives and embeddings are
weakest.

Both paths are backend-agnostic; only the search call differs (see backend.py).
"""
from __future__ import annotations

SEM_FLOOR = 0.675   # scores are on Neo4j's scale ((1+cos)/2), so this is raw cosine 0.35

# Keyword scores are NOT comparable across engines: Lucene (Neo4j) and bleve
# (Axon) both implement BM25 and both are free to scale it how they like — the
# same query scores 13.07 on one and 1.40 on the other. An absolute threshold
# therefore cannot be written once and mean the same thing twice.
#
# What does transfer is the shape of the result: a real match stands clear of
# the field, and noise is flat. Measured on both engines, in-scope questions
# put the winner 2.6-12.8x above the runner-up while out-of-scope questions sit
# at 1.1-1.2x. So the gate is a margin, not a score.
LEX_MARGIN = 1.5
W = {"cond": 1.0, "cause": 0.8, "cmd": 0.35}


def _has_vectors(db):
    try:
        return bool(db.run("MATCH (n:Condition) WHERE n.vec IS NOT NULL RETURN 1 LIMIT 1"))
    except Exception:
        return False


def _semantic(db, question):
    if not _has_vectors(db):
        return []
    from graphapp.embed import embed
    v = embed([question])[0]
    try:
        hits = db.search_vector("vec_cond", v, 3)
    except Exception:
        return []
    return [(h["name"], h["score"]) for h in hits if h["score"] >= SEM_FLOOR]


def _lexical(db, question):
    """Graph-aggregated keyword search: a cause or command votes for its condition."""
    score = {}

    def add(name, points, _family):
        score[name] = score.get(name, 0.0) + points

    def hits(index, limit):
        try:
            return [(h["name"], h["score"]) for h in db.search_text(index, question, limit)]
        except Exception:
            return []

    for name, s in hits("kb_cond", 5):
        add(name, s * W["cond"], "cond")
    for name, s in hits("kb_cause", 6):
        for r in db.run("MATCH (x:Condition)-[:CAUSED_BY]->(:RootCause {name:$n}) RETURN x.name AS n", n=name):
            add(r["n"], s * W["cause"], "cause")
    for name, s in hits("kb_cmd", 6):
        for r in db.run("MATCH (:Command {name:$n})-[:DIAGNOSES]->(x:Condition) RETURN x.name AS n", n=name):
            add(r["n"], s * W["cmd"], "cmd")

    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    if not ranked:
        return None, 0.0
    top, best = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    # The winner must stand clear of the field. Corroboration across families
    # was tried as an alternative and does not discriminate: an out-of-scope
    # question hits causes *and* commands precisely because common words match
    # broadly, so counting families lets exactly the wrong answers through.
    if runner_up <= 0 or best / runner_up >= LEX_MARGIN:
        return top, best
    return None, best


def resolve(db, question):
    """Return (condition name or None, confidence)."""
    sem = _semantic(db, question)
    if sem:
        return sem[0]
    return _lexical(db, question)
