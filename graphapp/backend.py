"""One app, two graph databases, almost no dialect.

Both speak Bolt, so the official Neo4j driver talks to either, and since Axon
v0.12.1 accepts Neo4j's schema DDL and index procedures the Cypher is now
*identical* on both — `CREATE FULLTEXT INDEX`, `db.index.fulltext.queryNodes`,
`db.index.vector.queryNodes` and `elementId()` all work either side. Anything
in this app that had to be written twice was an Axon compatibility gap, and it
was fixed rather than papered over.

One real difference remains, and it is a difference in the databases rather
than in their dialects:

**The class hierarchy.** Axon has a first-class ontology — declare `Pod` a
subclass of `K8sWorkload` once and `MATCH (n:K8sWorkload)` finds `:Pod` nodes,
with the planner resolving it. Neo4j labels are a flat set with no DDL for
hierarchy, so the loader materialises the closure: every node is written with
all of its ancestor labels. That works, and it costs — the hierarchy is frozen
at write time, so changing it means rewriting every node, and this graph pays
296 extra labels for the privilege.
"""
from __future__ import annotations
import os
from neo4j import GraphDatabase


class Backend:
    """Everything here is portable Cypher. Subclasses differ only in whether
    the database resolves subclasses itself."""

    #: does the database apply subclass inference at query time?
    infers_subclasses = False

    def __init__(self, name, uri, user=None, password=None, database=None):
        self.name = name
        self.uri = uri
        self._driver = GraphDatabase.driver(uri, auth=(user, password) if user else None)
        self._database = database

    def close(self):
        self._driver.close()

    def run(self, cypher, **params):
        with self._driver.session(database=self._database) as s:
            return [r.data() for r in s.run(cypher, **params)]

    def one(self, cypher, **params):
        rows = self.run(cypher, **params)
        return next(iter(rows[0].values())) if rows else None

    # --- identical on both, since v0.12.1 -----------------------------------
    def search_text(self, index, query, limit=6):
        return self.run(
            "CALL db.index.fulltext.queryNodes($i, $q) YIELD node, score "
            f"RETURN node.name AS name, score ORDER BY score DESC LIMIT {int(limit)}",
            i=index, q=query)

    def search_vector(self, index, vector, k=5):
        return self.run(
            "CALL db.index.vector.queryNodes($i, $k, $v) YIELD node, score "
            "RETURN node.name AS name, score",
            i=index, k=k, v=vector)

    def create_text_index(self, name, label, properties):
        props = ", ".join(f"n.{p}" for p in properties)
        self.run(f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) ON EACH [{props}]")

    def create_vector_index(self, name, label, prop, dims):
        self.run(f"""CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON n.{prop}
                     OPTIONS {{indexConfig: {{`vector.dimensions`: {dims},
                                              `vector.similarity_function`: 'cosine'}}}}""")

    def wipe(self):
        self.run("MATCH (n) DETACH DELETE n")


class AxonBackend(Backend):
    infers_subclasses = True

    def declare_ontology(self, classes, relations):
        """Teach Axon the hierarchy; it resolves subclasses from then on."""
        done = set()

        def emit(name):
            if name in done or name not in classes:
                return
            for parent in classes[name]["parents"]:
                emit(parent)
            done.add(name)
            self.run("CALL ontology.createClass($n, $p, $d)",
                     n=name, p=classes[name]["parents"], d=classes[name].get("description", ""))

        for name in sorted(classes):
            emit(name)
        for r in relations:
            self.run("CALL ontology.createRelation($n, $c)",
                     n=r["name"], c={k: v for k, v in r.items() if k != "name"})


class Neo4jBackend(Backend):
    infers_subclasses = False   # the loader materialises ancestor labels instead


def connect(kind=None):
    """Pick a backend from the environment.

        GRAPH_BACKEND = axon | neo4j        (default: axon)
        GRAPH_URI, GRAPH_USER, GRAPH_PASSWORD, GRAPH_DATABASE
    """
    kind = (kind or os.environ.get("GRAPH_BACKEND", "axon")).lower()
    uri = os.environ.get("GRAPH_URI")
    if kind == "axon":
        return AxonBackend("axon", uri or "bolt://127.0.0.1:7690",
                           os.environ.get("GRAPH_USER") or None,
                           os.environ.get("GRAPH_PASSWORD") or None)
    if kind == "neo4j":
        return Neo4jBackend("neo4j", uri or "bolt://127.0.0.1:7687",
                            os.environ.get("GRAPH_USER", "neo4j"),
                            os.environ.get("GRAPH_PASSWORD", "graphapp123"),
                            os.environ.get("GRAPH_DATABASE") or None)
    raise SystemExit(f"unknown backend {kind!r}; use axon or neo4j")
