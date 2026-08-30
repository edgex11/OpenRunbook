#!/usr/bin/env python3
"""Chat UI for the DevOps knowledge graph.

    GRAPH_BACKEND=neo4j python3 app.py        # http://127.0.0.1:7860

Gradio supplies the chat window, history and examples; everything below is
about turning a graph walk into readable Markdown. No language model is
involved — every line of an answer is a node or an edge.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gradio as gr
from graphapp.backend import connect
from graphapp.answer import ask, command_card

DB = connect()
RISK = {"read": "🟢", "mutate": "🟡", "destructive": "🔴"}
SCOPE = {"object": "single object", "namespace": "whole namespace",
         "cluster": "whole cluster", "account": "whole AWS account"}


def runbook(a, score):
    out = [f"### {a['condition']}", f"*{a['layer']} · observed on {a['seen']} · match {score}*", "", a["doc"]]
    if a["triage"]:
        out += ["", "**Triage — in this order**"]
        for i, s in enumerate(a["triage"], 1):
            out.append(f"{i}. `{s['command']}` {RISK.get(s['risk'],'')}")
            out.append(f"   ```\n   {s['syntax']}\n   ```")
            for look in (s.get("look_for") or []):
                if look and look.get("signal"):
                    out.append(f"   › look at **{look['look_in']}** — {look['means']}")
    if a["causes"]:
        out += ["", "**What is actually wrong**"]
        for c in a["causes"]:
            mech = f" *— {c['mechanism']}*" if c.get("mechanism") else ""
            out.append(f"- **{c['cause']}**{mech}  \n  {c['detail']}")
            cmds = [x for x in (c.get("commands") or []) if x]
            via = f"  ·  via {', '.join('`'+x+'`' for x in cmds)}" if cmds else ""
            out.append(f"  → {RISK.get(c['risk'],'')} {c['fix']}{via}")
    if a["prevent"]:
        out += ["", "**Stop it recurring**"]
        out += [f"- **{p['practice']}** — {p['why']}" for p in a["prevent"]]
    if a["related"]:
        out += ["", "**Same underlying mechanism elsewhere**"]
        out += [f"- {o['condition']} *({o['mechanism']})*" for o in a["related"]]
    return "\n".join(out)


def card(c, name):
    out = [f"### `{name}` {RISK.get(c['risk'],'')} {c['risk']}"]
    if c.get("status") == "deprecated":
        out += [f"> **Deprecated.** {c['deprecation']}"]
        if c.get("replaced_by"):
            out += [f"> Use `{c['replaced_by']}` instead."]
        return "\n".join(out)
    out += [f"```\n{c['syntax']}\n```", c["doc"]]
    targets = sorted(x for x in (c.get("targets") or []) if x)
    if targets:
        out.append(f"\n*Acts on: {', '.join(targets)}*")
    if c.get("scope"):
        affected = sorted(x for x in (c.get("affects") or []) if x)
        out += ["", f"**Blast radius:** {SCOPE.get(c['scope'], c['scope'])}"]
        if affected:
            out.append(f"**Can damage:** {', '.join(affected)}")
        out.append(f"**Reversible:** {c['reversible']} — {c['undo']}")
        if c.get("undone_by"):
            out.append(f"**Undo with:** `{c['undone_by']}`")
        out.append(f"**Check first:** {c['precheck']}")
        checks = sorted(x for x in (c.get("check_with") or []) if x)
        if checks:
            out.append(f"  ↳ run {', '.join('`'+x+'`' for x in checks)}")
    return "\n".join(out)


def respond(message, history):
    q = (message or "").strip()
    if not q:
        return "Ask about a symptom, or type `preflight <command>` to see what one does before you run it."
    if q.lower().startswith(("preflight ", "/preflight ")):
        term = q.split(" ", 1)[1]
        hits = DB.search_text("kb_cmd", term, 1)
        if not hits:
            return f"No command matching **{term}**."
        return card(command_card(DB, hits[0]["name"]), hits[0]["name"])
    r = ask(DB, q)
    if r["ok"]:
        return runbook(r["answer"], r["score"])
    lines = [f"I am not confident that matches a known failure *(best signal {r['score']})*."]
    if r.get("commands"):
        lines += ["", "Possibly relevant commands:"]
        lines += [f"- `{c['command']}` *({c['score']})*" for c in r["commands"]]
    else:
        lines += ["", "Try naming the symptom the platform prints, or a command verb."]
    return "\n".join(lines)


def banner():
    return (f"**backend** `{DB.name}` · {DB.one('MATCH (n) RETURN count(n)')} nodes, "
            f"{DB.one('MATCH ()-[r]->() RETURN count(r)')} relationships · "
            f"{DB.one('MATCH (c:Command) RETURN count(c)')} commands, "
            f"{DB.one('MATCH (c:Condition) RETURN count(c)')} failure modes · "
            f"subclasses {'inferred by the database' if DB.infers_subclasses else 'materialised at load'} · "
            f"search {'semantic + keyword' if DB.run('MATCH (n:Condition) WHERE n.vec IS NOT NULL RETURN 1 LIMIT 1') else 'keyword only'}")


demo = gr.ChatInterface(
    fn=respond,
    title="DevOps knowledge graph",
    description=f"Answers by walking the graph — no language model.  \n{banner()}",
    save_history=True,
    fill_height=True,
    examples=[
        "my pods keep restarting",
        "nodes drop packets when traffic spikes",
        "terraform says error acquiring the state lock",
        "container killed with exit code 137",
        "ingress never gets an external address",
        "dns lookups randomly take five seconds",
        "how do I safely take a node out of service",
        "preflight terraform destroy",
    ],
)

if __name__ == "__main__":
    demo.launch(server_name=os.environ.get("HOST", "127.0.0.1"),
                server_port=int(os.environ.get("PORT", "7860")))
