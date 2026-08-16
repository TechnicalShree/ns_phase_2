"""Compile the synthesizer with BootstrapFewShot (teacher/student split) and save the state.

    python compile_dspy.py            # ~20 suppliers, writes data/compiled_synthesizer.json

Writes data/compile_report.json with the uncompiled vs compiled judge pass rate and one
side-by-side output on a held-out supplier, so the improvement is shown and not just claimed.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import dspy
from dspy.teleprompt import BootstrapFewShot

from app import config
from app.engines import retrieve_all
from app.synthesize import RiskSynthesizer, judge, judge_failures, make_lm, pred_to_dict


def build_examples(names):
    with ThreadPoolExecutor(max_workers=6) as pool:
        contexts = list(pool.map(retrieve_all, names))
    return [
        dspy.Example(
            supplier_name=name,
            sql_context=ctx["sql"],
            doc_context=ctx["faiss"],
            graph_context=ctx["graph"],
        ).with_inputs("supplier_name", "sql_context", "doc_context", "graph_context")
        for name, ctx in zip(names, contexts)
    ]


def pass_rate(module, examples):
    passed, samples = 0, []
    for ex in examples:
        try:
            pred = module(**ex.inputs())
            fails = judge_failures(pred)
        except Exception as exc:  # noqa: BLE001
            fails = [f"call failed: {type(exc).__name__}: {exc}"]
            pred = None
        passed += not fails
        samples.append({"supplier": ex.supplier_name, "failures": fails,
                        "output": pred_to_dict(pred) if pred else None})
    return passed / max(1, len(examples)), samples


def main():
    manifest = json.loads(config.DATA_DIR.joinpath("manifest.json").read_text())
    held_out = manifest["held_out"]
    train_names = [n for n in manifest["suppliers"] if n != held_out]

    student_lm = make_lm(config.STUDENT_MODEL, temperature=0.2, max_tokens=1200)
    teacher_lm = make_lm(config.TEACHER_MODEL, temperature=0.7, max_tokens=1600)
    dspy.configure(lm=student_lm)

    print(f"student={config.STUDENT_MODEL}  teacher={config.TEACHER_MODEL}")
    print(f"building training set from {len(train_names)} suppliers (held out: {held_out}) ...")
    trainset = build_examples(train_names)
    devset = trainset[:6]
    heldout_ex = build_examples([held_out])[0]

    print("scoring the UNCOMPILED student ...")
    baseline = RiskSynthesizer()
    base_rate, base_samples = pass_rate(baseline, devset)
    base_heldout = baseline(**heldout_ex.inputs())

    print(f"baseline judge pass rate: {base_rate:.0%} — compiling with BootstrapFewShot ...")
    t0 = time.time()
    compiled = BootstrapFewShot(
        metric=judge, max_bootstrapped_demos=4, max_labeled_demos=0, max_rounds=1,
        teacher_settings=dict(lm=teacher_lm),
    ).compile(RiskSynthesizer(), trainset=trainset)
    took = time.time() - t0

    compiled.save(str(config.COMPILED_PATH))
    print(f"compiled in {took:.0f}s -> {config.COMPILED_PATH}")

    comp_rate, comp_samples = pass_rate(compiled, devset)
    comp_heldout = compiled(**heldout_ex.inputs())
    n_demos = len(getattr(compiled.think, "demos", []))

    config.TRACE_PATH.write_text(json.dumps({
        "student_model": config.STUDENT_MODEL,
        "teacher_model": config.TEACHER_MODEL,
        "train_size": len(trainset),
        "dev_size": len(devset),
        "bootstrapped_demos": n_demos,
        "compile_seconds": round(took, 1),
        "judge_pass_rate": {"uncompiled": base_rate, "compiled": comp_rate},
        "held_out_entity": held_out,
        "held_out_uncompiled": pred_to_dict(base_heldout),
        "held_out_uncompiled_failures": judge_failures(base_heldout),
        "held_out_compiled": pred_to_dict(comp_heldout),
        "held_out_compiled_failures": judge_failures(comp_heldout),
        "dev_samples": {"uncompiled": base_samples, "compiled": comp_samples},
    }, indent=2))

    print(f"judge pass rate  uncompiled {base_rate:.0%} -> compiled {comp_rate:.0%} "
          f"({n_demos} bootstrapped demos)")
    print(f"report -> {config.TRACE_PATH}")


if __name__ == "__main__":
    main()
