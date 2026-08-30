#!/usr/bin/env python3
"""Optional semantic search.

Neither database generates embeddings — both store vectors you supply. This
uses static embeddings (model2vec): numpy-only inference, no torch, no network
at query time, no per-query cost, and nothing leaves the machine.

    pip install model2vec
    GRAPH_BACKEND=neo4j python3 -m graphapp.embed

Measured on eval.py: full-text alone 55%, with this 72%.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from graphapp.backend import connect

MODEL = os.environ.get("GRAPH_EMBED_MODEL", "minishlab/potion-base-8M")
_model = None

# Which labels get vectors, and what text stands for each node.
TEXT = {
    "Condition":    "coalesce(n.name,'') + ' . ' + coalesce(n.doc,'') + ' . ' + coalesce(n.aliases,'')",
    "RootCause":    "coalesce(n.name,'') + ' . ' + coalesce(n.doc,'')",
    "Command":      "coalesce(n.name,'') + ' . ' + coalesce(n.doc,'') + ' . ' + coalesce(n.syntax,'')",
    "Practice":     "coalesce(n.name,'') + ' . ' + coalesce(n.doc,'')",
    "ResourceKind": "coalesce(n.name,'') + ' . ' + coalesce(n.doc,'') + ' . ' + coalesce(n.gotcha,'')",
}
INDEXES = [("vec_cond", "Condition"), ("vec_cause", "RootCause"), ("vec_cmd", "Command"),
           ("vec_practice", "Practice"), ("vec_kind", "ResourceKind")]


def model():
    global _model
    if _model is None:
        from model2vec import StaticModel
        _model = StaticModel.from_pretrained(MODEL)
    return _model


def embed(texts):
    return [[float(x) for x in v] for v in model().encode(list(texts))]


def drop_existing(db):
    """Both databases refuse a second vector index on the same label and
    property, so an existing one has to go before it can be replaced — for
    instance after changing embedding model, which changes the dimensions."""
    for name, _ in INDEXES:
        for stmt in (f"DROP INDEX {name} IF EXISTS", f"CALL vector.dropIndex('{name}')"):
            try:
                db.run(stmt)
                break
            except Exception:
                continue


def main():
    db = connect()
    drop_existing(db)
    total = 0
    for label, expr in TEXT.items():
        rows = db.run(f"MATCH (n:{label}) RETURN n.name AS name, {expr} AS text")
        if not rows:
            continue
        vecs = embed(r["text"] for r in rows)
        payload = [{"name": r["name"], "v": v} for r, v in zip(rows, vecs)]
        for i in range(0, len(payload), 200):
            db.run(f"UNWIND $rows AS r MATCH (n:{label} {{name: r.name}}) SET n.vec = r.v",
                   rows=payload[i:i + 200])
        total += len(rows)
        print(f"  {label}: {len(rows)}")
    dims = model().dim
    for name, label in INDEXES:
        try:
            db.create_vector_index(name, label, "vec", dims)
        except Exception as e:
            print(f"  ! {name}: {str(e)[:110]}")
    print(f"\nembedded {total} nodes with {MODEL} ({dims} dims) on {db.name}")
    print(f"vector indexes: {', '.join(n for n, _ in INDEXES)}")
    if db.name == "axon":
        print("  note: vec_cmd and vec_practice are declared on PARENT classes and cover their\n"
              "        subclasses, which needs Axon >= v0.12.1.")
    db.close()


if __name__ == "__main__":
    main()
