"""Three independent retrieval engines + the parallel fan-out.

Each query_* function returns a labeled context string and never raises: an unreachable or
empty store comes back as a tagged "[ENGINE] ERROR: ..." string so the synthesizer still runs.
"""

import functools
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

from . import config

# ---------------------------------------------------------------- SQL engine


def query_sql(entity: str) -> str:
    """Structured store: supplier master data + delivery/defect metrics."""
    try:
        if not config.DB_PATH.exists():
            return "[SQL] ERROR: suppliers.db not found — run generate_sandbox.py"
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM suppliers WHERE name = ? COLLATE NOCASE", (entity,)
        ).fetchone()
        if row is None:
            con.close()
            return f"[SQL] ERROR: no supplier named '{entity}' in the structured store"
        agg = con.execute(
            """SELECT COUNT(*) AS months, SUM(orders) AS orders, AVG(on_time_rate) AS otd,
                      MAX(defect_rate_ppm) AS worst_ppm, AVG(defect_rate_ppm) AS avg_ppm,
                      AVG(avg_lead_days) AS lead, SUM(credit_notes) AS credits
               FROM deliveries WHERE supplier_id = ?""",
            (row["id"],),
        ).fetchone()
        recent = con.execute(
            """SELECT month, on_time_rate, defect_rate_ppm, avg_lead_days, credit_notes
               FROM deliveries WHERE supplier_id = ? ORDER BY month DESC LIMIT 3""",
            (row["id"],),
        ).fetchall()
        con.close()

        lines = [
            f"[SQL] Supplier master + performance metrics for {row['name']}",
            f"- country={row['country']} | category={row['category']} | tier={row['tier']} "
            f"| onboarded={row['onboarded_year']} | annual_spend=${row['annual_spend_usd']:,}",
            f"- {agg['months']} months tracked, {agg['orders']} orders: avg on-time {agg['otd']:.1%}, "
            f"avg defects {agg['avg_ppm']:.0f} ppm (worst {agg['worst_ppm']} ppm), "
            f"avg lead time {agg['lead']:.1f} days, {agg['credits']} credit notes",
        ]
        for r in recent:
            lines.append(
                f"- {r['month']}: on-time {r['on_time_rate']:.1%}, {r['defect_rate_ppm']} ppm, "
                f"lead {r['avg_lead_days']} d, {r['credit_notes']} credit notes"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 — engine must degrade, not crash the request
        return f"[SQL] ERROR: {type(exc).__name__}: {exc}"


# -------------------------------------------------------------- FAISS engine


@functools.lru_cache(maxsize=1)
def _semantic_index():
    import faiss
    from rank_bm25 import BM25Okapi
    from sentence_transformers import SentenceTransformer

    docs = json.loads(config.META_PATH.read_text())
    index = faiss.read_index(str(config.FAISS_PATH))
    bm25 = BM25Okapi([d["text"].lower().split() for d in docs])
    return SentenceTransformer(config.EMBED_MODEL), index, docs, bm25


def _rrf(*ranked_lists, k=60):
    """Reciprocal rank fusion — the hybrid part of hybrid retrieval."""
    scores = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


def query_faiss(entity: str, top_k: int = 5) -> str:
    """Semantic store: hybrid BM25 (lexical) + dense (FAISS) retrieval fused with RRF."""
    try:
        if not config.FAISS_PATH.exists():
            return "[FAISS] ERROR: index not found — run generate_sandbox.py"
        model, index, docs, bm25 = _semantic_index()
        query = f"{entity} supplier quality defects delivery contract risk"

        dense_ids = index.search(
            model.encode([query], normalize_embeddings=True).astype("float32"), top_k * 6
        )[1][0].tolist()
        bm25_scores = bm25.get_scores(query.lower().split())
        bm25_ids = sorted(range(len(docs)), key=lambda i: bm25_scores[i], reverse=True)[: top_k * 6]

        fused = _rrf(dense_ids, bm25_ids)
        # exact metadata match first, semantic neighbours as the fallback tail
        exact = [i for i in fused if docs[i]["supplier"].lower() == entity.lower()]
        exact_set = set(exact)
        rest = [i for i in fused if i not in exact_set]
        picked = (exact + rest)[:top_k]
        if not picked:
            return f"[FAISS] ERROR: no documents retrieved for '{entity}'"

        lines = [f"[FAISS] Hybrid BM25+dense (RRF) document evidence for {entity}"]
        for i in picked:
            d = docs[i]
            tag = "exact-match" if i in exact else "semantic-neighbour"
            lines.append(f"- ({d['doc_type']}, {tag}, re: {d['supplier']}) {d['text']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"[FAISS] ERROR: {type(exc).__name__}: {exc}"


# -------------------------------------------------------------- Neo4j engine

GRAPH_CYPHER = """
MATCH (s:Supplier {name: $name})
OPTIONAL MATCH (s)-[:OWNED_BY]->(p:Parent)
OPTIONAL MATCH (s)-[:OPERATES_AT]->(site:Site)<-[:OPERATES_AT]-(co:Supplier)
OPTIONAL MATCH (s)-[:SUPPLIES_TO]->(b:Buyer)
OPTIONAL MATCH (s)-[d:DEPENDS_ON]->(up:Supplier)
OPTIONAL MATCH (down:Supplier)-[:DEPENDS_ON]->(s)
RETURN s.country AS country, s.watchlist AS watchlist,
       p.name AS parent, p.jurisdiction AS jurisdiction,
       collect(DISTINCT site.name) AS sites,
       collect(DISTINCT co.name) AS site_neighbours,
       collect(DISTINCT b.name) AS buyers,
       collect(DISTINCT up.name + ' (' + d.component + ')') AS depends_on,
       collect(DISTINCT down.name) AS dependents,
       collect(DISTINCT CASE WHEN up.watchlist THEN up.name END) AS flagged_upstream
"""


def query_graph(entity: str) -> str:
    """Graph store: ownership, shared sites, buyer exposure and sub-tier dependencies."""
    driver = None
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
            connection_timeout=8,
        )
        with driver.session(database=config.NEO4J_DATABASE) as ses:
            rec = ses.run(GRAPH_CYPHER, name=entity).single()
        if rec is None:
            return f"[GRAPH] ERROR: no :Supplier node named '{entity}'"

        flagged = [x for x in rec["flagged_upstream"] if x]
        neighbours = [x for x in rec["site_neighbours"] if x and x != entity]
        lines = [
            f"[GRAPH] Neo4j relationship context for {entity}",
            f"- ownership: {rec['parent'] or 'independent'}"
            + (f" (jurisdiction: {rec['jurisdiction']})" if rec["parent"] else ""),
            f"- on internal watchlist: {'yes' if rec['watchlist'] else 'no'}",
            f"- operates at: {', '.join(rec['sites']) or 'unknown'}; co-located suppliers: "
            f"{', '.join(neighbours) or 'none'}",
            f"- supplies to buyers: {', '.join(rec['buyers']) or 'none'}",
            f"- depends on sub-tier suppliers: {', '.join(x for x in rec['depends_on'] if x) or 'none'}",
            f"- suppliers depending on it: {', '.join(x for x in rec['dependents'] if x) or 'none'}",
            f"- watchlisted upstream dependencies: {', '.join(flagged) or 'none'}",
        ]
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 — Neo4j down must not fail the request
        return f"[GRAPH] ERROR: {type(exc).__name__}: {exc}"
    finally:
        if driver is not None:
            driver.close()


# ------------------------------------------------------------ parallel fan-out


ENGINES = {"sql": query_sql, "faiss": query_faiss, "graph": query_graph}


def retrieve_all_timed(entity: str) -> tuple[dict, dict]:
    """Fire all three engines simultaneously; total latency ≈ the slowest single engine."""

    def timed(fn):
        t0 = time.perf_counter()
        return fn(entity), round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = {k: f.result() for k, f in {k: pool.submit(timed, fn) for k, fn in ENGINES.items()}.items()}
    contexts = {k: v[0] for k, v in results.items()}
    timings = {k: v[1] for k, v in results.items()}
    timings["parallel_total"] = round(time.perf_counter() - t0, 3)
    timings["sum_of_engines"] = round(sum(v[1] for v in results.values()), 3)
    return contexts, timings


def retrieve_all(entity: str) -> dict:
    return retrieve_all_timed(entity)[0]
