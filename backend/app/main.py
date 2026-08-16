import json
import sqlite3
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config, engines, synthesize

app = FastAPI(title="Supplier Risk — Multi-Source Retrieval", version="2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


class AssessRequest(BaseModel):
    entity: str


@app.get("/health")
def health():
    stores = {}
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        stores["sqlite"] = {"ok": True, "suppliers": con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]}
        con.close()
    except Exception as exc:  # noqa: BLE001
        stores["sqlite"] = {"ok": False, "error": str(exc)}
    try:
        stores["faiss"] = {"ok": True, "chunks": len(json.loads(config.META_PATH.read_text()))}
    except Exception as exc:  # noqa: BLE001
        stores["faiss"] = {"ok": False, "error": str(exc)}
    graph = engines.query_graph("__healthcheck__")
    stores["neo4j"] = {"ok": "ERROR: no :Supplier node" in graph, "detail": graph.splitlines()[0][:160]}
    return {
        "status": "ok",
        "stores": stores,
        "dspy_compiled": config.COMPILED_PATH.exists(),
        "models": {"student": config.STUDENT_MODEL, "teacher": config.TEACHER_MODEL},
    }


@app.get("/entities")
def entities():
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        names = [r[0] for r in con.execute("SELECT name FROM suppliers ORDER BY name")]
        con.close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"structured store unavailable: {exc}") from exc
    held_out = json.loads(config.DATA_DIR.joinpath("manifest.json").read_text()).get("held_out")
    return {"entities": names, "held_out": held_out}


@app.post("/assess")
def assess(req: AssessRequest):
    entity = req.entity.strip()
    if not entity:
        raise HTTPException(400, "entity is required")

    contexts, timings = engines.retrieve_all_timed(entity)
    if all(c.startswith(f"[{k.upper()}] ERROR") for k, c in
           (("sql", contexts["sql"]), ("faiss", contexts["faiss"]), ("graph", contexts["graph"]))):
        raise HTTPException(404, f"no store has any data for '{entity}'")

    t0 = time.perf_counter()
    try:
        module = synthesize.get_module()
        pred = module(
            supplier_name=entity,
            sql_context=contexts["sql"],
            doc_context=contexts["faiss"],
            graph_context=contexts["graph"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"synthesis failed: {type(exc).__name__}: {exc}") from exc
    timings["synthesis"] = round(time.perf_counter() - t0, 3)

    failures = synthesize.judge_failures(pred)
    return {
        "entity": entity,
        "contexts": contexts,
        "degraded": [k for k, c in contexts.items() if "ERROR" in c.split("\n")[0]],
        "verdict": synthesize.pred_to_dict(pred),
        "judge": {"passed": not failures, "failures": failures},
        "compiled": module._compiled_state,
        "timings": timings,
    }


@app.get("/compile-report")
def compile_report():
    report = synthesize.compile_report()
    if not report:
        raise HTTPException(404, "not compiled yet — run compile_dspy.py")
    return report


@app.get("/judge-demo")
def judge_demo():
    """Evidence that the judge rejects bad output: one good, one deliberately broken."""
    class P:  # noqa: D401 — throwaway stand-in for a dspy.Prediction
        def __init__(self, **kw):
            self.__dict__.update(kw)

    bad = P(verdict="MAYBE", risk_score=140, key_risks="risky",
            rationale="As an AI I cannot assess this supplier.")
    good = P(verdict="FLAG", risk_score=78,
             key_risks="on-time delivery averaged 71% over 8 months; 18400 ppm defect peak in 2026-05; "
                       "upstream dependency Aureus Polymers is watchlisted",
             rationale="SQL metrics show on-time delivery of 71% and defects peaking at 18400 ppm across "
                       "8 tracked months. The FAISS document evidence adds two escalated late shipments and a "
                       "conditional-pass audit. The Neo4j graph shows a watchlisted sub-tier dependency, so the "
                       "exposure is not isolated to this supplier.")
    return {
        "rejected_example": {"output": bad.__dict__, "failures": synthesize.judge_failures(bad)},
        "accepted_example": {"output": good.__dict__, "failures": synthesize.judge_failures(good)},
    }
