"""Evaluarea componentelor AI din 4Health.

Rulează din folderul api/:

    python -m eval.run_eval --task dosage    --methods rules qwen2.5:3b llama3.2:3b
    python -m eval.run_eval --task triage    --methods rules qwen2.5:3b gemma2:2b
    python -m eval.run_eval --task retrieval                       # embedding vs lexical (necesită `python -m app.seed`)
    python -m eval.run_eval --task all       --methods rules qwen2.5:3b

`rules` = baseline determinist; orice alt nume = model Ollama.
Rezultatele se salvează în eval/results/ (JSON cu predicțiile + un tabel Markdown).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.ai import dosage, rag, triage
from app.ai.ollama_client import LLMError, OllamaClient
from app.ai.safety import check_red_flags

HERE = Path(__file__).parent
DATA = HERE / "datasets"
RESULTS = HERE / "results"


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def _eq(field: str, pred, gold) -> bool:
    if field == "timing":
        return sorted(pred or []) == sorted(gold or [])
    if isinstance(gold, (int, float)) and isinstance(pred, (int, float)):
        return abs(float(pred) - float(gold)) < 1e-6
    return pred == gold


def _client(model: str) -> OllamaClient:
    return OllamaClient(model=model)


# --------------------------------------------------------------------------- dosage
def eval_dosage(methods: list[str]) -> dict:
    rows = load("dosage")
    report = {}
    for m in methods:
        llm = None if m == "rules" else _client(m)
        field_ok, exact, lat, errors, preds = Counter(), 0, [], 0, []
        for r in rows:
            t0 = time.perf_counter()
            try:
                pred = dosage.extract_rules(r["text"]) if llm is None else dosage.extract_llm(r["text"], llm)
            except LLMError as e:
                errors += 1
                pred = dosage.empty()
                print(f"  [{m}] eroare: {e}")
            lat.append((time.perf_counter() - t0) * 1000)
            ok = {f: _eq(f, pred[f], r["expected"][f]) for f in dosage.FIELDS}
            field_ok.update(f for f, v in ok.items() if v)
            exact += all(ok.values())
            preds.append({"text": r["text"], "pred": pred, "gold": r["expected"], "wrong": [f for f, v in ok.items() if not v]})
        n = len(rows)
        report[m] = {
            "n": n,
            "exact_match": exact / n,
            "field_accuracy": {f: field_ok[f] / n for f in dosage.FIELDS},
            "macro_field_accuracy": sum(field_ok.values()) / (n * len(dosage.FIELDS)),
            "latency_ms_median": statistics.median(lat),
            "errors": errors,
            "predictions": preds,
        }
        print(f"[dosage] {m:<16} exact={report[m]['exact_match']:.2f} fields={report[m]['macro_field_accuracy']:.3f} "
              f"lat={report[m]['latency_ms_median']:.0f}ms")
    return report


# --------------------------------------------------------------------------- triage
def _macro_f1(golds: list[str], preds: list[str]) -> float:
    labels = set(golds)
    f1s = []
    for c in labels:
        tp = sum(g == p == c for g, p in zip(golds, preds))
        fp = sum(p == c and g != c for g, p in zip(golds, preds))
        fn = sum(g == c and p != c for g, p in zip(golds, preds))
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0)
    return sum(f1s) / len(f1s)


def eval_triage(methods: list[str]) -> dict:
    rows = load("triage")
    # 1) Stratul de siguranță (determinist, același pentru toate metodele)
    tp = fp = fn = tn = 0
    misses = []
    for r in rows:
        flagged = check_red_flags(r["text"]).urgent
        tp += flagged and r["urgent"]; fp += flagged and not r["urgent"]
        fn += (not flagged) and r["urgent"]; tn += (not flagged) and not r["urgent"]
        if flagged != r["urgent"]:
            misses.append(r["text"])
    safety = {"recall": tp / (tp + fn) if tp + fn else 1.0, "precision": tp / (tp + fp) if tp + fp else 1.0,
              "false_negatives_and_positives": misses}
    print(f"[triage] red flags  recall={safety['recall']:.2f} precision={safety['precision']:.2f}")

    # 2) Clasificarea specializării (doar cazurile non-urgente)
    cases = [r for r in rows if not r["urgent"]]
    report = {"safety_rules": safety}
    for m in methods:
        llm = None if m == "rules" else _client(m)
        preds, lat, errors = [], [], 0
        for r in cases:
            t0 = time.perf_counter()
            try:
                p = triage.classify_rules(r["text"]) if llm is None else triage.classify_llm(r["text"], llm)[0]
            except LLMError as e:
                errors += 1
                p = "medicina_familie"
                print(f"  [{m}] eroare: {e}")
            lat.append((time.perf_counter() - t0) * 1000)
            preds.append(p)
        golds = [r["specialty"] for r in cases]
        confusion = defaultdict(Counter)
        for g, p in zip(golds, preds):
            confusion[g][p] += 1
        report[m] = {
            "n": len(cases),
            "accuracy": sum(g == p for g, p in zip(golds, preds)) / len(cases),
            "macro_f1": _macro_f1(golds, preds),
            "latency_ms_median": statistics.median(lat),
            "errors": errors,
            "mistakes": [{"text": r["text"], "gold": g, "pred": p} for r, g, p in zip(cases, golds, preds) if g != p],
            "confusion": {g: dict(c) for g, c in confusion.items()},
        }
        print(f"[triage] {m:<16} acc={report[m]['accuracy']:.2f} macroF1={report[m]['macro_f1']:.3f} "
              f"lat={report[m]['latency_ms_median']:.0f}ms")
    return report


# --------------------------------------------------------------------------- retrieval
def eval_retrieval(k: int = 4) -> dict:
    from app.db import SessionLocal

    rows = load("retrieval")
    report = {}
    with SessionLocal() as db:
        for name, llm in [("lexical", None), ("embedding", OllamaClient())]:
            hits_at_k, rr, lat, sims = 0, [], [], []
            details = []
            for r in rows:
                t0 = time.perf_counter()
                res = rag.search(db, r["query"], llm, k=k)
                lat.append((time.perf_counter() - t0) * 1000)
                titles = [h.title for h in res]
                rank = titles.index(r["relevant_title"]) + 1 if r["relevant_title"] in titles else None
                hits_at_k += rank is not None
                rr.append(1 / rank if rank else 0)
                if rank and name == "embedding":
                    sims.append(res[rank - 1].score)
                details.append({"query": r["query"], "gold": r["relevant_title"], "retrieved": titles, "rank": rank})
            report[name] = {"n": len(rows), f"recall@{k}": hits_at_k / len(rows), "mrr": sum(rr) / len(rows),
                            "latency_ms_median": statistics.median(lat), "details": details}
            if sims:
                report[name]["relevant_similarity_min"] = min(sims)
                report[name]["relevant_similarity_median"] = statistics.median(sims)
            print(f"[retrieval] {name:<10} recall@{k}={report[name][f'recall@{k}']:.2f} MRR={report[name]['mrr']:.3f}")
    return report


# --------------------------------------------------------------------------- output
def to_markdown(task: str, rep: dict) -> str:
    lines = [f"## {task} - {datetime.now():%Y-%m-%d %H:%M}", ""]
    if task == "dosage":
        lines += ["| metodă | exact match | acuratețe medie câmpuri | latență mediană |", "|---|---|---|---|"]
        lines += [f"| {m} | {r['exact_match']:.2f} | {r['macro_field_accuracy']:.3f} | {r['latency_ms_median']:.0f} ms |" for m, r in rep.items()]
        lines += ["", "Acuratețe pe câmp:", "", "| metodă | " + " | ".join(dosage.FIELDS) + " |", "|---" * (len(dosage.FIELDS) + 1) + "|"]
        lines += [f"| {m} | " + " | ".join(f"{r['field_accuracy'][f]:.2f}" for f in dosage.FIELDS) + " |" for m, r in rep.items()]
    elif task == "triage":
        s = rep["safety_rules"]
        lines += [f"Red flags (reguli): recall **{s['recall']:.2f}**, precision {s['precision']:.2f}", "",
                  "| metodă | acuratețe | macro-F1 | latență mediană |", "|---|---|---|---|"]
        lines += [f"| {m} | {r['accuracy']:.2f} | {r['macro_f1']:.3f} | {r['latency_ms_median']:.0f} ms |"
                  for m, r in rep.items() if m != "safety_rules"]
    elif task == "retrieval":
        k = next(x for x in next(iter(rep.values())) if x.startswith("recall@"))
        lines += [f"| metodă | {k} | MRR | latență mediană |", "|---|---|---|---|"]
        lines += [f"| {m} | {r[k]:.2f} | {r['mrr']:.3f} | {r['latency_ms_median']:.0f} ms |" for m, r in rep.items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["dosage", "triage", "retrieval", "all"], default="all")
    ap.add_argument("--methods", nargs="+", default=["rules", "qwen2.5:3b"])
    args = ap.parse_args()

    tasks = ["dosage", "triage", "retrieval"] if args.task == "all" else [args.task]
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = []
    for t in tasks:
        rep = eval_dosage(args.methods) if t == "dosage" else eval_triage(args.methods) if t == "triage" else eval_retrieval()
        (RESULTS / f"{stamp}_{t}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        md.append(to_markdown(t, rep))
    out = RESULTS / f"{stamp}_summary.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print("\n" + "\n".join(md))
    print(f"Salvat în {out}")


if __name__ == "__main__":
    main()
