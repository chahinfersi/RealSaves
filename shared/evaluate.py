"""Measure how well each embedding model finds YOUR reels — read-only, nothing is written.

    python shared/evaluate.py                     # e5-small only
    python shared/evaluate.py e5-small bge-m3     # compare models (bge-m3: 2.3 GB first download)

How it works: load every `ready` reel from MongoDB, embed them all in memory (no Atlas index
needed — with ~100 reels, brute force is instant), then for each topic in eval_topics.json
rank the reels and compare with the reels you said SHOULD come back.

The two numbers to read:
  found in top 5  — of the reels that should come back, how many made the top 5
                    (the reels Claude would actually see)
  best cut-off    — the score that best separates "relevant" from "not relevant"
                    (becomes find_reels' threshold)
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import numpy as np
from dotenv import load_dotenv
from pymongo import MongoClient

import embedding
from reels import passage_text

HERE = Path(__file__).resolve().parent
TOP_K = 5


def load_reels():
    load_dotenv(HERE.parent / ".env")
    collection = MongoClient(os.environ["MONGODB_URI"])[os.environ["DB_NAME"]]["saves"]
    return list(collection.find({"status": "ready"}).sort("capturedAt", 1))


def label(doc, n=55):
    text = " ".join((doc.get("caption") or doc.get("title") or doc.get("transcript") or "").split())
    return text[:n]


def best_cutoff(relevant, irrelevant):
    """Try every observed score as a cut-off; keep the one with the best F1.

    F1 balances two failures: missing a relevant reel (cut-off too high) and showing a
    wrong one (cut-off too low).
    """
    best = (0.0, 0.0)
    for t in sorted(set(relevant) | set(irrelevant)):
        kept_good = sum(s >= t for s in relevant)
        kept_bad = sum(s >= t for s in irrelevant)
        if kept_good == 0:
            continue
        precision = kept_good / (kept_good + kept_bad)
        recall = kept_good / len(relevant)
        f1 = 2 * precision * recall / (precision + recall)
        best = max(best, (f1, t))
    return best


def evaluate(model_name, docs, topics):
    embedder = embedding.Embedder(model_name)
    texts = [passage_text(d) for d in docs]

    t0 = time.perf_counter()
    vectors = embedder.embed_passages(texts)
    seconds = time.perf_counter() - t0
    cut = sum(embedder.count_tokens(t) >= embedder.spec["max_tokens"] for t in texts)

    print(f"\n{'═' * 78}\n{model_name}  ({embedder.spec['dimensions']} dims)  "
          f"embedded {len(docs)} reels in {seconds:.0f}s · {cut} reel(s) cut at {embedder.spec['max_tokens']} tokens")

    ids = [str(d["_id"]) for d in docs]
    found = wanted = 0
    relevant_scores, irrelevant_scores = [], []
    for topic in topics:
        scores = vectors @ embedder.embed_query(topic["query"])
        expected = set(topic["expected"])
        top = scores.argsort()[::-1][:TOP_K]
        hits = sum(ids[i] in expected for i in top)
        found += hits
        wanted += min(TOP_K, len(expected))
        for i, s in enumerate(scores):
            (relevant_scores if ids[i] in expected else irrelevant_scores).append(float(s))

        result = f"{hits}/{min(TOP_K, len(expected))}" if expected else "no match expected"
        print(f"\n  {topic['name']}  → {result}")
        for i in top:
            mark = "✓" if ids[i] in expected else "·"
            print(f"     {mark} {scores[i]:.3f}  {label(docs[i])}")

    f1, cutoff = best_cutoff(relevant_scores, irrelevant_scores)
    print(f"\n  SUMMARY {model_name}: found in top {TOP_K} = {found}/{wanted} ({found / wanted:.0%}) · "
          f"best cut-off ≈ {cutoff:.3f} (F1 {f1:.2f}) · "
          f"relevant avg {np.mean(relevant_scores):.3f} vs others avg {np.mean(irrelevant_scores):.3f}")
    return found, wanted, cutoff, f1


def main():
    models = sys.argv[1:] or [embedding.DEFAULT_MODEL]
    topics_file = HERE / "eval_topics.json"
    if not topics_file.exists():
        sys.exit("No shared/eval_topics.json yet: copy shared/eval_topics.example.json and fill "
                 "it with your own topics and the _id of the reels each one should find.")
    topics = json.loads(topics_file.read_text(encoding="utf-8"))["topics"]
    docs = load_reels()
    print(f"{len(docs)} ready reels · {len(topics)} topics · models: {', '.join(models)}")

    results = {m: evaluate(m, docs, topics) for m in models}
    print(f"\n{'═' * 78}\nFINAL")
    for m, (found, wanted, cutoff, f1) in results.items():
        print(f"  {m:<10} top-{TOP_K}: {found}/{wanted} ({found / wanted:.0%})   cut-off ≈ {cutoff:.3f}   F1 {f1:.2f}")


if __name__ == "__main__":
    main()
