"""DSPy signature, module, judge metric, and compiled-state loading.

One compiled ChainOfThought call turns the three raw contexts into one structured verdict.
No agent loop, no tool calling — that is Phase 3.
"""

import functools
import json
import re

import dspy

from . import config

VERDICTS = {"APPROVE", "FLAG"}
BANNED = ["as an ai", "i cannot", "i do not have access", "language model", "lorem ipsum", "n/a"]


class SupplierRiskVerdict(dspy.Signature):
    """Assess a supplier using three independent retrieval contexts.

    Weigh the structured metrics, the document evidence and the relationship graph against each
    other. Cite concrete numbers and named entities. FLAG a supplier whose delivery, quality or
    network exposure is materially risky; otherwise APPROVE.
    """

    supplier_name: str = dspy.InputField(desc="the supplier being assessed")
    sql_context: str = dspy.InputField(desc="structured delivery/defect metrics from SQLite")
    doc_context: str = dspy.InputField(desc="contract, complaint and audit text from hybrid FAISS retrieval")
    graph_context: str = dspy.InputField(desc="ownership, buyer and sub-tier dependency edges from Neo4j")

    verdict: str = dspy.OutputField(desc="exactly one of: APPROVE, FLAG")
    risk_score: int = dspy.OutputField(desc="integer 0-100; 0 is no risk, 100 is critical risk")
    key_risks: str = dspy.OutputField(desc="2-4 risks, semicolon-separated, each citing a number or named entity")
    rationale: str = dspy.OutputField(desc="3-4 sentences that reference all three retrieval sources by name")


class RiskSynthesizer(dspy.Module):
    def __init__(self):
        super().__init__()
        self.think = dspy.ChainOfThought(SupplierRiskVerdict)

    def forward(self, supplier_name, sql_context, doc_context, graph_context):
        return self.think(
            supplier_name=supplier_name,
            sql_context=sql_context,
            doc_context=doc_context,
            graph_context=graph_context,
        )


# ------------------------------------------------------------- judge metric


def judge(example, pred, trace=None) -> bool:
    """Rule-based judge: structure, required fields, value ranges, and hard content rules."""
    return not judge_failures(pred)


def judge_failures(pred) -> list[str]:
    """Same rules as judge(), but returns why it failed — used by /judge-demo and tests."""
    fails = []
    verdict = str(getattr(pred, "verdict", "")).strip().upper()
    risks = str(getattr(pred, "key_risks", "") or "")
    rationale = str(getattr(pred, "rationale", "") or "")

    if verdict not in VERDICTS:
        fails.append(f"verdict must be one of {sorted(VERDICTS)}, got '{verdict}'")

    try:
        score = int(re.search(r"-?\d+", str(getattr(pred, "risk_score", ""))).group())
    except (AttributeError, TypeError, ValueError):
        fails.append("risk_score is not an integer")
        score = None
    if score is not None and not 0 <= score <= 100:
        fails.append(f"risk_score {score} out of range 0-100")

    if len(rationale.split()) < 25:
        fails.append("rationale shorter than 25 words")
    if len([r for r in risks.split(";") if r.strip()]) < 2:
        fails.append("fewer than 2 key risks listed")
    if not re.search(r"\d", risks + rationale):
        fails.append("no concrete figure cited from the retrieved contexts")

    blob = f"{risks} {rationale}".lower()
    for word in BANNED:
        if word in blob:
            fails.append(f"banned phrase in output: '{word}'")

    # hard consistency rule: a FLAG must be backed by a high score, and vice versa
    if score is not None and verdict in VERDICTS:
        if verdict == "FLAG" and score < 50:
            fails.append(f"verdict FLAG contradicts risk_score {score} (<50)")
        if verdict == "APPROVE" and score >= 70:
            fails.append(f"verdict APPROVE contradicts risk_score {score} (>=70)")
    return fails


# --------------------------------------------------------------- LM wiring


def make_lm(model: str, **kw):
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return dspy.LM(model, api_key=config.OPENROUTER_API_KEY, api_base="https://openrouter.ai/api/v1", **kw)


@functools.lru_cache(maxsize=1)
def get_module():
    """The student module, with compiled state loaded if compile_dspy.py has been run."""
    dspy.configure(lm=make_lm(config.STUDENT_MODEL, temperature=0.2, max_tokens=1200))
    module = RiskSynthesizer()
    if config.COMPILED_PATH.exists():
        module.load(str(config.COMPILED_PATH))
        module._compiled_state = True
    else:
        module._compiled_state = False
    return module


def pred_to_dict(pred) -> dict:
    return {
        "verdict": str(pred.verdict).strip().upper(),
        "risk_score": pred.risk_score,
        "key_risks": [r.strip() for r in str(pred.key_risks).split(";") if r.strip()],
        "rationale": str(pred.rationale).strip(),
        "reasoning": str(getattr(pred, "reasoning", "")).strip(),
    }


def explain_failure(exc: Exception) -> dict:
    """Turn an LLM-provider exception into something a UI can show a human."""
    text = str(exc)
    if not config.OPENROUTER_API_KEY:
        reason, hint = "no_api_key", "Set OPENROUTER_API_KEY in backend/.env and restart the API."
    elif "free-models-per-day" in text or "RateLimit" in type(exc).__name__:
        reason = "rate_limited"
        hint = ("The OpenRouter free-model daily allowance is used up. It resets at 00:00 UTC, or add "
                "credits to raise the cap. Retrieval below is unaffected.")
    elif "more credits" in text or "402" in text:
        reason, hint = "out_of_credits", "The OpenRouter account is out of credits — top it up to synthesize."
    elif "not found" in text.lower() or "404" in text:
        reason, hint = "model_unavailable", f"Model {config.STUDENT_MODEL} is not available on this account."
    else:
        reason, hint = "llm_error", "The language model call failed; the three retrieval contexts are still shown."
    return {"reason": reason, "hint": hint, "model": config.STUDENT_MODEL,
            "detail": f"{type(exc).__name__}: {text[:400]}"}


def compile_report() -> dict:
    if config.TRACE_PATH.exists():
        return json.loads(config.TRACE_PATH.read_text())
    return {}
