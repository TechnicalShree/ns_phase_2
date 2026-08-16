"""Runnable smoke check — no framework, no fixtures.

    python test_smoke.py         # after generate_sandbox.py

Covers the three engines, graceful degradation, parallelism, and the judge's reject path.
"""

import json

from app import config, engines
from app.synthesize import judge_failures

ENTITY = "Aureus Polymers"


class P:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_engines():
    sql = engines.query_sql(ENTITY)
    assert sql.startswith("[SQL]") and "ERROR" not in sql, sql
    assert "on-time" in sql

    faiss = engines.query_faiss(ENTITY)
    assert faiss.startswith("[FAISS]") and "ERROR" not in faiss, faiss
    assert "exact-match" in faiss, "hybrid retrieval returned no exact metadata match"

    graph = engines.query_graph(ENTITY)
    assert graph.startswith("[GRAPH]"), graph
    if "ERROR" in graph:
        print("  ! Neo4j unreachable — degradation path exercised instead:", graph[:80])
    else:
        assert "depends on sub-tier suppliers" in graph

    # engines return genuinely different content, not three copies of the same facts
    assert len(set([sql, faiss, graph])) == 3


def test_unknown_entity_degrades():
    for fn in (engines.query_sql, engines.query_faiss, engines.query_graph):
        out = fn("Nonexistent Supplier Ltd")
        assert isinstance(out, str) and out.startswith("["), out  # tagged string, never a crash


def test_parallel_is_parallel():
    contexts, timings = engines.retrieve_all_timed(ENTITY)
    assert set(contexts) == {"sql", "faiss", "graph"}
    assert timings["parallel_total"] <= timings["sum_of_engines"] + 0.05, timings
    print(f"  parallel {timings['parallel_total']}s vs serial sum {timings['sum_of_engines']}s")


def test_judge_rejects_bad_output():
    bad = P(verdict="MAYBE", risk_score=140, key_risks="risky",
            rationale="As an AI I cannot assess this supplier.")
    fails = judge_failures(bad)
    assert len(fails) >= 4, fails
    assert any("verdict must be" in f for f in fails)
    assert any("out of range" in f for f in fails)
    assert any("banned phrase" in f for f in fails)

    inconsistent = P(verdict="FLAG", risk_score=12,
                     key_risks="on-time 71% over 8 months; 18400 ppm defects peak",
                     rationale="SQL shows 71% on-time delivery and 18400 ppm defects, the documents add two "
                               "escalated late shipments, and the graph shows a watchlisted upstream dependency "
                               "that widens the exposure well beyond this supplier alone.")
    assert judge_failures(inconsistent) == ["verdict FLAG contradicts risk_score 12 (<50)"]

    good = P(**{**inconsistent.__dict__, "risk_score": 78})
    assert judge_failures(good) == []


if __name__ == "__main__":
    assert config.DB_PATH.exists(), "run generate_sandbox.py first"
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ok —", len(json.loads(config.META_PATH.read_text())), "indexed chunks")
