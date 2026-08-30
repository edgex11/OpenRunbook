#!/usr/bin/env python3
"""Load the DevOps graph into whichever backend is configured.

    GRAPH_BACKEND=neo4j python3 -m graphapp.load
    GRAPH_BACKEND=axon  python3 -m graphapp.load

The data is one `axon export` dump. What differs per backend is how the class
hierarchy is represented: Axon is told the hierarchy and infers over it; Neo4j
gets every node's ancestor labels written onto it, because it has no such
concept. That difference is the whole reason this file has a branch in it.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from graphapp.backend import connect

DATA = Path(__file__).resolve().parent.parent / "data"
BATCH = 500

TEXT_INDEXES = [
    ("kb_cond", "Condition", ["name", "doc", "aliases"]),
    ("kb_cause", "RootCause", ["name", "doc"]),
    ("kb_cmd", "Command", ["name", "doc", "syntax", "example"]),
]


def ancestors(classes, label, seen=None):
    """Every class at or above `label`."""
    seen = seen if seen is not None else set()
    if label in seen or label not in classes:
        return seen
    seen.add(label)
    for p in classes[label]["parents"]:
        ancestors(classes, p, seen)
    return seen


def label_set(classes, labels, materialise):
    """The labels a node is written with.

    Axon gets what the data says and resolves the rest at query time. Neo4j
    gets the transitive closure, because a label there means only itself.
    """
    if not materialise:
        return sorted(labels)
    out = set()
    for l in labels:
        out |= ancestors(classes, l)
    return sorted(out)


def main():
    ont = json.loads((DATA / "ontology.json").read_text())
    classes, relations = ont["classes"], ont["relations"]
    records = [json.loads(l) for l in (DATA / "graph.jsonl").read_text().splitlines() if l.strip()]
    nodes = [r for r in records if r["type"] == "node"]
    rels = [r for r in records if r["type"] == "relationship"]

    db = connect()
    print(f"backend: {db.name}  (subclass inference: {'built in' if db.infers_subclasses else 'materialised at load'})")
    t0 = time.time()
    db.wipe()

    if db.infers_subclasses:
        db.declare_ontology(classes, relations)
        print(f"  ontology: {len(classes)} classes, {len(relations)} relation axioms declared")

    # Nodes. `gid` is the export's id, used only to reconnect relationships;
    # both databases assign their own internal ids on write.
    materialise = not db.infers_subclasses
    by_labels = {}
    for n in nodes:
        key = tuple(label_set(classes, n["labels"], materialise))
        by_labels.setdefault(key, []).append({"gid": n["id"], "props": n["properties"]})
    written = 0
    for labels, rows in by_labels.items():
        tag = "".join(f":`{l}`" for l in labels)
        for i in range(0, len(rows), BATCH):
            db.run(f"UNWIND $rows AS r CREATE (n{tag}) SET n = r.props SET n.gid = r.gid",
                   rows=rows[i:i + BATCH])
            written += len(rows[i:i + BATCH])
    extra = sum(len(k) for k in by_labels) - sum(len(n["labels"]) for n in nodes)
    print(f"  nodes: {written}" + (f"  (+{extra} materialised ancestor labels)" if materialise and extra > 0 else ""))

    # Relationships are reconnected by the export's own ids. At this size the
    # lookup is cheap; on a larger dump you would index gid first.
    by_type = {}
    for r in rels:
        by_type.setdefault(r["relType"], []).append(
            {"s": r["start"], "e": r["end"], "props": r.get("properties") or {}})
    made = 0
    for rtype, rows in by_type.items():
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            db.run(f"""UNWIND $rows AS r
                       MATCH (a {{gid: r.s}}) MATCH (b {{gid: r.e}})
                       CREATE (a)-[x:`{rtype}`]->(b) SET x = r.props""", rows=chunk)
            made += len(chunk)
    print(f"  relationships: {made} across {len(by_type)} types")

    for name, label, props in TEXT_INDEXES:
        try:
            db.create_text_index(name, label, props)
        except Exception as e:
            print(f"  ! text index {name}: {str(e)[:110]}")
    print(f"  full-text indexes: {', '.join(n for n, _, _ in TEXT_INDEXES)}")

    print(f"\n  loaded in {time.time()-t0:.1f}s")
    print(f"  nodes            {db.one('MATCH (n) RETURN count(n)')}")
    print(f"  relationships    {db.one('MATCH ()-[r]->() RETURN count(r)')}")
    print(f"  :Command         {db.one('MATCH (c:Command) RETURN count(c)')}"
          f"   <- {'via ontology inference' if db.infers_subclasses else 'via materialised labels'}")
    db.close()
    print("\nnext:  python3 -m graphapp.embed     (optional, adds semantic search)")
    print("       python3 server.py               (web UI on :8080)")


if __name__ == "__main__":
    main()
