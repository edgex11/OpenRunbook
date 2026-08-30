# graphapp

A DevOps question-answering app over a graph database. Ask *"my pods keep
restarting"* and get the triage commands in order, what to look for in each
one's output, the causes with their fixes, and the practice that stops it
recurring — assembled by walking the graph, with **no language model involved**.

Runs on **Neo4j** (open source) or on **Axon**. Same code, same Cypher, same
data. Preloaded with a real knowledge graph of AWS, Kubernetes and Terraform
operations.

```
512 nodes · 917 edges · 140 classes
125 commands · 24 failure modes · 68 causes and fixes · 29 practices
```

## Run it locally

Either database. Pick one, load, ask.

### Neo4j

```bash
docker run -d --name graphapp-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/graphapp123 neo4j:5.26-community

pip install -r requirements.txt
GRAPH_BACKEND=neo4j python3 -m graphapp.load     # 512 nodes, ~3s
GRAPH_BACKEND=neo4j python3 -m graphapp.embed    # optional: semantic search
GRAPH_BACKEND=neo4j python3 app.py               # http://127.0.0.1:7860
```

### Axon

```bash
axon serve --data ./devops-data --listen :7480 --bolt :7690

pip install -r requirements.txt
GRAPH_BACKEND=axon python3 -m graphapp.load
GRAPH_BACKEND=axon python3 -m graphapp.embed
GRAPH_BACKEND=axon python3 app.py
```

Configure with `GRAPH_URI`, `GRAPH_USER`, `GRAPH_PASSWORD`, `GRAPH_DATABASE`.
Needs Axon **v0.12.1+**, which is where Neo4j's index DDL and index procedures
landed.

Try: *my pods keep restarting* · *nodes drop packets when traffic spikes* ·
*terraform says error acquiring the state lock* · *container killed with exit
code 137* · or `preflight terraform destroy` for a command's blast radius.

## What it looks like

The chat UI is Gradio's `ChatInterface`, so the app contains no frontend code —
`app.py` is 130 lines and almost all of it turns a graph walk into Markdown.

Ask about a symptom and you get the condition, the triage sequence with the
signal to look for in each command's output, every cause with its fix and risk
tier, the practices that prevent it, and the other failures that share the same
underlying mechanism. Ask `preflight <command>` and you get its blast radius,
what it can damage, whether it is reversible, and what to check first.

## One app, two databases

Both speak Bolt, so the official Neo4j driver talks to either, and every query
in this app is identical on both. One real difference remains, and it is a
difference between the databases rather than between dialects:

**The class hierarchy.** Axon has a first-class ontology — declare `Pod` a
subclass of `K8sWorkload` once and `MATCH (n:K8sWorkload)` finds `:Pod` nodes,
resolved by the planner. Neo4j labels are a flat set with no DDL for hierarchy,
so the loader materialises the closure: every node is written with all of its
ancestor labels. It works, and it costs — this graph pays **296 extra labels**,
and the hierarchy is frozen at write time, so changing it means rewriting every
node.

```
axon    nodes: 512                                    :Command 125  (inferred)
neo4j   nodes: 512  (+296 materialised ancestor labels)   :Command 125  (materialised)
```

Everything else that once needed two code paths turned out to be an Axon
compatibility gap, and was fixed rather than papered over: `CREATE FULLTEXT
INDEX`, `CREATE VECTOR INDEX`, `SHOW INDEXES`, `db.index.fulltext.queryNodes`,
`db.index.vector.queryNodes`, `elementId()`, `ORDER BY` on a grouping key after
aggregation, and the similarity-score scale.

## Retrieval

Semantic first, keyword as fallback — a cascade, not a blend. Rank fusion was
tried and measured *worse* than either signal alone: a vector index always
returns k neighbours however irrelevant, so it answers confidently when the
question is out of scope, while BM25 on a short question against long documents
is dominated by common words.

`eval.py` asks 29 questions phrased the way an engineer types them — not the
way the graph stores them — plus two that are out of scope and must be refused.

| backend | score |
|---|---|
| Axon | 21/29 (72%) |
| Neo4j | 22/29 (76%) |

**The two should agree, and when they did not it was a real bug.** Identical
questions once produced different answers because the databases scaled cosine
similarity differently — Neo4j maps it to `(1+cos)/2`, Axon returned raw cosine
— so a threshold meant two different things. Keyword scores genuinely are not
comparable across engines (Lucene and bleve both implement BM25 and both scale
it as they like: the same query scores 13.07 on one and 1.40 on the other), so
the lexical gate is a **margin over the runner-up** rather than an absolute
score. A real match stands clear of the field at 2.6–12.8×; noise sits at
1.1–1.2×. The remaining difference between the two columns is one networking
question the two engines rank differently.

## Layout

```
app.py               chat UI (Gradio) + Markdown rendering
graphapp/backend.py  the two backends, and the one place they differ
graphapp/load.py     loads the dump into either database
graphapp/retrieve.py question -> failure (semantic cascade over keyword)
graphapp/answer.py   the graph walk that becomes an answer
graphapp/embed.py    optional semantic layer, local and offline
eval.py              the honest benchmark
data/graph.jsonl     the knowledge, as an `axon export` dump
data/ontology.json   the class hierarchy, shared by both backends
```

## The knowledge

`data/graph.jsonl` is one JSON object per node or relationship, so it greps and
diffs like any other text. Each failure carries the words operators actually
type separately from the platform's own wording; each destructive command
carries its blast radius and a mandatory precheck; facts that changed between
versions carry a gate, so the app will not suggest `terraform taint`
(deprecated) or PodSecurityPolicy (removed in 1.25). Deprecated commands are
kept on purpose — the graph has to know the wrong way in order to redirect you.

Curated operational knowledge, cross-checked against upstream Kubernetes, AWS
EKS and HashiCorp documentation, carrying a `verified_on` date. A starting
point for your own environment, not gospel: your cluster has its own gotchas,
and those are the ones worth adding.
