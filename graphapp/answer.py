"""Building the answer by walking the graph.

No language model is involved. Every line of the answer is a node or an edge:
the triage commands in the order they should be run, what to look for in each
one's output, the possible causes with their fixes, the practice that prevents
recurrence, and the other symptoms that share the same underlying mechanism.

The Cypher here is portable — it runs unchanged on Neo4j and on Axon.
"""
from __future__ import annotations
from graphapp.retrieve import resolve


def condition(db, name):
    head = db.run("MATCH (x:Condition {name:$n}) RETURN x.doc AS doc, x.layer AS layer, x.seen_on AS seen",
                  n=name)
    if not head:
        return None
    out = {"condition": name, **head[0]}

    out["triage"] = db.run("""
        MATCH (c:Command)-[d:DIAGNOSES]->(:Condition {name:$n})
        OPTIONAL MATCH (c)-[:REVEALS]->(e:Evidence)
        RETURN c.name AS command, c.syntax AS syntax, c.risk AS risk, d.order AS step,
               collect({signal: e.name, look_in: e.look_in, means: e.means})[0..2] AS look_for
        ORDER BY d.order""", n=name)

    out["causes"] = db.run("""
        MATCH (:Condition {name:$n})-[:CAUSED_BY]->(rc:RootCause)-[:REMEDIATED_BY]->(f:Remediation)
        OPTIONAL MATCH (rc)-[:MECHANISM]->(m:Mechanism)
        OPTIONAL MATCH (f)-[:USES_COMMAND]->(cmd:Command)
        RETURN rc.name AS cause, rc.doc AS detail, f.name AS fix, f.risk AS risk,
               m.name AS mechanism, collect(DISTINCT cmd.name) AS commands""", n=name)

    out["prevent"] = db.run("""
        MATCH (:Condition {name:$n})-[:CAUSED_BY]->(rc)<-[:MITIGATES]-(p:Practice)
        RETURN DISTINCT p.name AS practice, p.doc AS why""", n=name)

    out["related"] = db.run("""
        MATCH (:Condition {name:$n})-[:CAUSED_BY]->(:RootCause)-[:MECHANISM]->(m:Mechanism)
              <-[:MECHANISM]-(:RootCause)<-[:CAUSED_BY]-(o:Condition)
        WHERE o.name <> $n
        RETURN DISTINCT o.name AS condition, m.name AS mechanism""", n=name)
    return out


def command_card(db, name):
    """Blast radius, reversibility and what to check before running something."""
    rows = db.run("""
        MATCH (c:Command {name:$n})
        OPTIONAL MATCH (c)-[:REPLACED_BY]->(new:Command)
        OPTIONAL MATCH (c)-[:GUARDED_BY]->(:Precondition)-[:USES_COMMAND]->(g:Command)
        OPTIONAL MATCH (c)-[:TARGETS]->(k:ResourceKind)
        OPTIONAL MATCH (c)-[:AFFECTS]->(a:ResourceKind)
        OPTIONAL MATCH (c)-[:UNDONE_BY]->(u:Command)
        RETURN c.syntax AS syntax, c.doc AS doc, c.risk AS risk, c.tool AS tool,
               c.blast_scope AS scope, c.reversible AS reversible, c.undo AS undo,
               c.precheck AS precheck, c.status AS status, c.deprecation AS deprecation,
               new.name AS replaced_by, u.name AS undone_by,
               collect(DISTINCT g.name) AS check_with,
               collect(DISTINCT k.name) AS targets,
               collect(DISTINCT a.name) AS affects""", n=name)
    return rows[0] if rows else None


def ask(db, question):
    name, score = resolve(db, question)
    if name:
        return {"ok": True, "score": round(score, 3), "answer": condition(db, name)}
    try:
        near = [{"command": h["name"], "score": round(h["score"], 2)}
                for h in db.search_text("kb_cmd", question, 3) if h["score"] >= 0.25]
    except Exception:
        near = []
    return {"ok": False, "score": round(score, 3), "commands": near}
