"""Populate all three stores (SQLite, FAISS, Neo4j) with one synthetic supplier manifest.

    python generate_sandbox.py            # all three stores
    python generate_sandbox.py --skip-graph

Deterministic: same seed -> same sandbox, so the DSPy training set is reproducible.
"""

import argparse
import json
import random
import sqlite3

from app import config

SEED = 20260816

PARENTS = ["Hanwei Holdings", "Vertex Industrial Group", "Nordkap AS", "Meridian Capital Partners"]
COUNTRIES = ["India", "Vietnam", "Germany", "Mexico", "China", "Poland", "Malaysia"]
CATEGORIES = ["injection molding", "PCB assembly", "sheet metal", "packaging", "wire harness", "sterile consumables"]
BUYERS = ["Aurora Medical", "Northwind Devices", "Cobalt Robotics", "Helio Diagnostics"]
SITES = ["Pune SEZ", "Hai Phong Zone 4", "Guadalajara Park", "Katowice Estate", "Shenzhen Bao'an"]

NAMES = [
    "Aureus Polymers", "Baltec Precision", "Cygnus Circuits", "Delvia Packaging", "Elmwood Metals",
    "Fenmore Plastics", "Grayline Assembly", "Hexa Wireworks", "Ironvale Stamping", "Juniper Sterile",
    "Kestrel Components", "Lumen Molding", "Marnix Electronics", "Norvent Tooling", "Orbis Cartons",
    "Pyrite Fabrication", "Quandel Harness", "Ravello Circuits", "Serrata Devices", "Tolmen Plastics",
    "Ulmer Sheetworks", "Vantis Packaging", "Westford Boards", "Xiora Molding", "Yarrow Consumables",
    "Zenkai Precision",
]

# entity #26 (Zenkai Precision) is held out of the DSPy training manifest — see compile_dspy.py
HELD_OUT = NAMES[-1]

CONTRACT_TEMPLATES = [
    "Master supply agreement with {name} sets a {sla}% on-time delivery SLA with liquidated damages of "
    "{ld}% of order value per late week. Termination for convenience requires {notice} days notice.",
    "Quality addendum: {name} must hold ISO 9001 certification and permit unannounced audits. Current "
    "certificate expires in {exp} months. Corrective action window is {cap} days after a defect notice.",
    "Pricing schedule for {name} is fixed for {fix} months with a raw-material pass-through clause capped "
    "at {cap_pct}% per year. Volume rebate of {rebate}% applies above {vol} units per quarter.",
]

COMPLAINT_TEMPLATES = [
    "Incoming inspection rejected lot {lot} from {name}: {defect}. Line stopped for {hrs} hours at the "
    "{buyer} program. Supplier attributed it to a tooling change made without notification.",
    "{buyer} escalated a shipment from {name} that arrived {days} days late without an advance ship notice. "
    "This is the {nth} late shipment this quarter; expedite freight was charged back.",
    "Audit note for {name}: {finding}. Auditor rated the site {rating}. Follow-up audit recommended within "
    "{months} months.",
]

DEFECTS = [
    "flash on 12% of parts and two short shots", "solder voiding above IPC class 3 limits",
    "burr height out of tolerance on the mating edge", "mixed lot codes inside a single carton",
    "sterile barrier seal peel strength below spec",
]
FINDINGS = [
    "calibration records for two CMMs were expired", "no documented supplier-change notification process",
    "housekeeping and FOD control were exemplary", "operator training records were complete and current",
    "corrective actions from the prior audit remain open",
]


def build_manifest(rng):
    suppliers = []
    for i, name in enumerate(NAMES):
        risky = i % 4 == 0  # every 4th supplier is seeded as a weak performer
        suppliers.append({
            "id": i + 1,
            "name": name,
            "country": rng.choice(COUNTRIES),
            "category": rng.choice(CATEGORIES),
            "annual_spend_usd": rng.randrange(200_000, 9_000_000, 50_000),
            "tier": rng.choice([1, 1, 2]),
            "onboarded_year": rng.randint(2012, 2024),
            "parent": rng.choice(PARENTS) if i % 3 == 0 else None,
            "site": rng.choice(SITES),
            "buyers": rng.sample(BUYERS, rng.randint(1, 3)),
            "risky": risky,
        })
    return suppliers


def write_sqlite(suppliers, rng):
    config.DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.executescript("""
        CREATE TABLE suppliers (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, country TEXT, category TEXT,
            annual_spend_usd INTEGER, tier INTEGER, onboarded_year INTEGER
        );
        CREATE TABLE deliveries (
            supplier_id INTEGER, month TEXT, orders INTEGER, on_time_rate REAL,
            defect_rate_ppm INTEGER, avg_lead_days REAL, credit_notes INTEGER
        );
    """)
    months = [f"2026-{m:02d}" for m in range(1, 9)]
    rows = 0
    for s in suppliers:
        con.execute(
            "INSERT INTO suppliers VALUES (?,?,?,?,?,?,?)",
            (s["id"], s["name"], s["country"], s["category"], s["annual_spend_usd"], s["tier"], s["onboarded_year"]),
        )
        base_otd = rng.uniform(0.62, 0.83) if s["risky"] else rng.uniform(0.88, 0.99)
        base_ppm = rng.randint(4000, 22000) if s["risky"] else rng.randint(100, 2500)
        for month in months:
            con.execute(
                "INSERT INTO deliveries VALUES (?,?,?,?,?,?,?)",
                (
                    s["id"], month, rng.randint(4, 60),
                    round(min(1.0, max(0.4, base_otd + rng.uniform(-0.06, 0.06))), 3),
                    max(0, int(base_ppm * rng.uniform(0.6, 1.5))),
                    round(rng.uniform(9, 45), 1),
                    rng.randint(2, 9) if s["risky"] else rng.randint(0, 2),
                ),
            )
            rows += 1
    con.commit()
    con.close()
    return len(suppliers), rows


def build_documents(suppliers, rng):
    docs = []
    for s in suppliers:
        for t in CONTRACT_TEMPLATES:
            docs.append({
                "supplier": s["name"], "doc_type": "contract",
                "text": t.format(
                    name=s["name"], sla=rng.randint(92, 99), ld=rng.randint(1, 5), notice=rng.choice([30, 60, 90]),
                    exp=rng.randint(1, 24), cap=rng.choice([10, 15, 30]), fix=rng.choice([6, 12, 24]),
                    cap_pct=rng.randint(2, 8), rebate=rng.randint(1, 6), vol=rng.randrange(5000, 50000, 1000),
                ),
            })
        for t in rng.sample(COMPLAINT_TEMPLATES, 2) + ([COMPLAINT_TEMPLATES[0]] if s["risky"] else []):
            docs.append({
                "supplier": s["name"], "doc_type": "complaint" if "audit" not in t.lower() else "audit",
                "text": t.format(
                    name=s["name"], lot=f"L{rng.randrange(10000, 99999)}", defect=rng.choice(DEFECTS),
                    hrs=rng.randint(2, 40), buyer=rng.choice(s["buyers"]), days=rng.randint(3, 21),
                    nth=rng.choice(["second", "third", "fifth"]), finding=rng.choice(FINDINGS),
                    rating=rng.choice(["conditional pass", "pass", "fail"]) if s["risky"] else "pass",
                    months=rng.choice([3, 6, 12]),
                ),
            })
    return docs


def write_faiss(docs):
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(config.EMBED_MODEL)
    vecs = model.encode([d["text"] for d in docs], normalize_embeddings=True, show_progress_bar=False)
    vecs = np.asarray(vecs, dtype="float32")
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    faiss.write_index(index, str(config.FAISS_PATH))
    config.META_PATH.write_text(json.dumps(docs))
    return len(docs)


def write_neo4j(suppliers, rng):
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
    nodes = edges = 0
    with driver.session(database=config.NEO4J_DATABASE) as ses:
        ses.run("MATCH (n) DETACH DELETE n")
        for s in suppliers:
            ses.run(
                "MERGE (s:Supplier {name:$name}) SET s.country=$country, s.category=$category, "
                "s.tier=$tier, s.watchlist=$watchlist",
                name=s["name"], country=s["country"], category=s["category"], tier=s["tier"],
                watchlist=bool(s["risky"]),
            )
            nodes += 1
            if s["parent"]:
                ses.run(
                    "MERGE (p:Parent {name:$parent}) SET p.jurisdiction=$j "
                    "WITH p MATCH (s:Supplier {name:$name}) MERGE (s)-[:OWNED_BY]->(p)",
                    parent=s["parent"], name=s["name"], j=rng.choice(["Singapore", "Cayman Islands", "Norway", "Delaware"]),
                )
                edges += 1
            ses.run("MERGE (f:Site {name:$site}) WITH f MATCH (s:Supplier {name:$name}) MERGE (s)-[:OPERATES_AT]->(f)",
                    site=s["site"], name=s["name"])
            edges += 1
            for b in s["buyers"]:
                ses.run("MERGE (b:Buyer {name:$b}) WITH b MATCH (s:Supplier {name:$name}) "
                        "MERGE (s)-[:SUPPLIES_TO]->(b)", b=b, name=s["name"])
                edges += 1
        # sub-tier dependencies between suppliers — this is the information only the graph has
        for s in suppliers:
            for dep in rng.sample([x for x in suppliers if x["name"] != s["name"]], rng.randint(1, 3)):
                ses.run(
                    "MATCH (a:Supplier {name:$a}), (b:Supplier {name:$b}) "
                    "MERGE (a)-[r:DEPENDS_ON]->(b) SET r.component=$c",
                    a=s["name"], b=dep["name"], c=rng.choice(["resin", "connectors", "castings", "film", "PCBs"]),
                )
                edges += 1
        counts = ses.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        rels = ses.run("MATCH ()-[r]->() RETURN count(r) AS r").single()["r"]
    driver.close()
    return counts, rels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-graph", action="store_true", help="populate SQLite + FAISS only")
    args = ap.parse_args()

    rng = random.Random(SEED)
    suppliers = build_manifest(rng)
    config.DATA_DIR.joinpath("manifest.json").write_text(
        json.dumps({"suppliers": [s["name"] for s in suppliers], "held_out": HELD_OUT}, indent=2)
    )

    n_sup, n_rows = write_sqlite(suppliers, rng)
    print(f"SQLite : {n_sup} suppliers, {n_rows} delivery rows -> {config.DB_PATH}")

    docs = build_documents(suppliers, rng)
    print(f"FAISS  : {write_faiss(docs)} chunks embedded -> {config.FAISS_PATH}")

    if args.skip_graph:
        print("Neo4j  : skipped (--skip-graph)")
    else:
        try:
            nodes, rels = write_neo4j(suppliers, rng)
            print(f"Neo4j  : {nodes} nodes, {rels} relationships -> {config.NEO4J_URI}")
        except Exception as exc:  # noqa: BLE001 — SQL+FAISS are already written; graph can be seeded later
            print(f"Neo4j  : FAILED ({type(exc).__name__}: {exc}) — fix NEO4J_* and re-run this script")

    print(f"Held-out entity (not used for DSPy training): {HELD_OUT}")


if __name__ == "__main__":
    main()
