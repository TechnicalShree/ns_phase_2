"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

const PANELS = [
  ["sql", "SQLite — structured metrics"],
  ["faiss", "FAISS — hybrid BM25 + dense"],
  ["graph", "Neo4j — relationship graph"],
];

export default function Page() {
  const [entities, setEntities] = useState([]);
  const [entity, setEntity] = useState("");
  const [result, setResult] = useState(null);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API}/entities`)
      .then((r) => r.json())
      .then((d) => {
        setEntities(d.entities || []);
        setEntity(d.entities?.[0] || "");
      })
      .catch((e) => setError(`cannot reach API at ${API}: ${e.message}`));
    fetch(`${API}/compile-report`).then((r) => (r.ok ? r.json() : null)).then(setReport).catch(() => {});
  }, []);

  async function assess() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const r = await fetch(`${API}/assess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.statusText);
      setResult(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const v = result?.verdict;

  return (
    <main>
      <h1>Supplier Risk — Multi-Source Retrieval</h1>
      <p className="sub">
        Three engines fire in parallel for the same supplier; a compiled DSPy ChainOfThought module
        synthesizes one structured verdict from all three raw contexts below.
      </p>

      <div className="row">
        <select value={entity} onChange={(e) => setEntity(e.target.value)}>
          {entities.map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
        <button onClick={assess} disabled={busy || !entity}>
          {busy ? "Retrieving + synthesizing…" : "Assess supplier"}
        </button>
        {report && (
          <span className="badge">
            DSPy compiled · {report.bootstrapped_demos} demos · judge pass{" "}
            {Math.round(report.judge_pass_rate.uncompiled * 100)}% → {Math.round(report.judge_pass_rate.compiled * 100)}%
          </span>
        )}
      </div>

      {error && <p className="err">{error}</p>}

      {result && (
        <>
          <div className="grid">
            {PANELS.map(([key, title]) => (
              <div className="panel" key={key}>
                <h2>{title}</h2>
                <pre className={result.contexts[key].includes("] ERROR") ? "err" : ""}>
                  {result.contexts[key]}
                </pre>
              </div>
            ))}
          </div>

          <div className="panel" style={{ marginTop: 14 }}>
            <h2>Synthesized verdict</h2>
            <div className="verdict">
              <span className={`tag ${v.verdict}`}>{v.verdict}</span>
              <span>risk score {v.risk_score}/100</span>
              <span className="badge">{result.compiled ? "compiled module" : "uncompiled module"}</span>
              <span className="badge">
                judge {result.judge.passed ? "passed" : `failed: ${result.judge.failures.join("; ")}`}
              </span>
            </div>
            <ul>
              {v.key_risks.map((k, i) => (
                <li key={i}>{k}</li>
              ))}
            </ul>
            <p>{v.rationale}</p>
            {v.reasoning && (
              <details>
                <summary>chain-of-thought trace</summary>
                <pre>{v.reasoning}</pre>
              </details>
            )}
            <p className="meta">
              parallel retrieval {result.timings.parallel_total}s (serial sum would be{" "}
              {result.timings.sum_of_engines}s) · sql {result.timings.sql}s · faiss {result.timings.faiss}s ·
              graph {result.timings.graph}s · synthesis {result.timings.synthesis}s
              {result.degraded.length > 0 && ` · degraded engines: ${result.degraded.join(", ")}`}
            </p>
          </div>
        </>
      )}
    </main>
  );
}
