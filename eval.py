#!/usr/bin/env python3
"""How often does a plain-language question reach the right failure?

    GRAPH_BACKEND=neo4j python3 eval.py
    GRAPH_BACKEND=axon  python3 eval.py

The questions are phrased the way an on-call engineer types them, deliberately
NOT reusing the wording stored in the graph. That distinction is the whole
point: an earlier hand-picked set scored 4/4 purely because those phrasings had
been written into the alias text. Write the test set first, and freshly, or the
number measures your own tuning.

Two out-of-scope questions are included. Refusing them counts: a confidently
wrong answer is worse than no answer.

The score should be the same on both backends. If it is not, that is a
portability bug worth chasing -- it is how the similarity-score scale
difference between Neo4j and Axon was found.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graphapp.backend import connect
from graphapp.answer import ask

TESTS = [
 ("the checkout deployment won't come up, containers start then die","CrashLoopBackOff"),
 ("kubelet says it can't get the container image","ImagePullBackOff"),
 ("new pods are stuck and never get scheduled","PodPending"),
 ("java process reaped exit 137","OOMKilled"),
 ("curl to the clusterip times out even though replicas are healthy","ServiceHasNoEndpoints"),
 ("my ingress never gets an external address","IngressNoAddress"),
 ("statefulset can't start, its claim is stuck","PVCPending"),
 ("one worker went unhealthy and pods moved off it","NodeNotReady"),
 ("deleting the staging namespace hangs forever","NamespaceStuckTerminating"),
 ("node upgrade is blocked and nothing is evicting","DrainStuck"),
 ("pod gets AccessDenied calling s3 despite the role","IRSAAccessDenied"),
 ("terraform won't run, says lock id held","TerraformStateLocked"),
 ("plan shows diffs nobody wrote in code","TerraformDrift"),
 ("apply fails because the bucket is already there","TerraformResourceAlreadyExists"),
 ("terraform wants to replace my database, why","TerraformUnexpectedDestroy"),
 ("autoscaler shows unknown and never scales","HPANoMetrics"),
 ("cordon and drain a node without breaking things","DrainStuck"),
 ("image tag missing from the registry","ImagePullBackOff"),
 ("readiness probe failing so nothing gets traffic","ServiceHasNoEndpoints"),
 ("how much does an s3 bucket cost", None),   # out of scope - must refuse
 ("write me a dockerfile", None),             # out of scope - must refuse
]

NET_TESTS = [
 ("scaling up and new pods can't get addresses","PodIPExhaustion"),
 ("every http call to the service adds five seconds","DNSLatencyOrFailure"),
 ("app can't resolve anything since we locked down egress","NetworkPolicyBlocksTraffic"),
 ("nodes drop packets when traffic spikes","ConntrackTableFull"),
 ("calls to a third party api fail sporadically from the cluster","SNATPortExhaustion"),
 ("load balancer says targets unhealthy though the app responds locally","LoadBalancerTargetsUnhealthy"),
 ("file downloads freeze partway but the page loads","MTUBlackhole"),
 ("our inter-zone data transfer bill doubled","CrossAZTrafficCost"),
 ("services in different namespaces stopped talking","NetworkPolicyBlocksTraffic"),
 ("subnet ran out of ip addresses","PodIPExhaustion"),
]


def score(db, tests, label):
    right = wrong = missed = ref_ok = ref_total = 0
    failures = []
    for q, want in tests:
        r = ask(db, q)
        got = r["answer"]["condition"] if r["ok"] else None
        if want is None:
            ref_total += 1
            ref_ok += got is None
            if got is not None:
                failures.append((r["score"], q, "refuse", got))
        elif got == want:
            right += 1
        else:
            failures.append((r["score"], q, want, got))
            missed += got is None
            wrong += got is not None
    total = len([t for t in tests if t[1]])
    print(f"{label}: {right}/{total} ({100*right/total:.0f}%)  wrong {wrong}  missed {missed}" +
          (f"  |  out-of-scope refused {ref_ok}/{ref_total}" if ref_total else ""))
    for sc, q, want, got in failures:
        print(f"    x {sc:.2f}  {q[:50]:52} -> {got}   (want {want})")
    return right, total


if __name__ == "__main__":
    db = connect()
    semantic = bool(db.run("MATCH (n:Condition) WHERE n.vec IS NOT NULL RETURN 1 LIMIT 1"))
    print(f"backend {db.name} · search {'semantic + keyword' if semantic else 'keyword only'}"
          f"{'' if semantic else '  (run: python3 -m graphapp.embed)'}\n")
    a = score(db, TESTS, "core      ")
    print()
    b = score(db, NET_TESTS, "networking")
    print(f"\nCOMBINED: {a[0]+b[0]}/{a[1]+b[1]} ({100*(a[0]+b[0])/(a[1]+b[1]):.0f}%)")
    db.close()
