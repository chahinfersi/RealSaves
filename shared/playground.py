"""Embeddings playground — SEE vector search work before any database is involved.

Run it (from the project root; the first run downloads the model, ~490 MB, once):

    python shared/playground.py

It embeds a few fake "reels" in Arabic, French and English, then ranks them for a few
questions, exactly like find_reels will — but in memory, with plain numpy.
Read the output next to ../VECTOR-SEARCH-EXPLAINED.md §2–3.
"""

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console + Arabic text
except (AttributeError, ValueError):
    pass

import embedding

E = embedding.Embedder()  # the default model (see embedding.DEFAULT_MODEL)

REELS = {
    "R1 AR google": "كيف تظهر في أول نتائج جوجل؟ أول شيء: اختر كلمة مفتاحية واحدة لكل صفحة واكتبها في العنوان.",
    "R2 EN hooks": "Five hooks that stop the scroll in the first two seconds of your video.",
    "R3 FR crêpes": "La recette de crêpes de ma grand-mère : farine, œufs, lait et une pincée de sel.",
    "R4 EN speed": "Your website loads slowly and Google punishes you for it. Compress your images first.",
    "R5 FR SEO": "Comment améliorer le référencement naturel de votre site sans payer de publicité.",
    "R6 AR cooking": "طريقة تحضير الكسكسي التونسي بالخضار واللحم خطوة بخطوة.",
}

QUESTIONS = [
    "SEO for our landing page",        # should find R1, R4, R5 (three languages!)
    "how to cook a quick dinner",      # should find R3, R6
    "how to fix a car engine",         # NOTHING relevant exists → watch the scores anyway
]


def main():
    t0 = time.perf_counter()
    names = list(REELS)
    vectors = E.embed_passages(list(REELS.values()))  # the "write path"
    print(f"Embedded {len(names)} reels in {time.perf_counter() - t0:.1f}s (includes loading the model)")

    v = vectors[0]
    print(f"\nWhat one vector looks like ({names[0]}):")
    print(f"  {len(v)} numbers: [{', '.join(f'{x:.4f}' for x in v[:5])}, … {len(v) - 5} more]")
    print(f"  length = {float((v ** 2).sum() ** 0.5):.3f}  (normalized to 1 → cosine is just a dot product)")

    for question in QUESTIONS:  # the "read path"
        t0 = time.perf_counter()
        q = E.embed_query(question)
        scores = vectors @ q  # 6 dot products at once = 6 cosine similarities (brute force)
        ms = (time.perf_counter() - t0) * 1000
        print(f"\nQuestion: {question!r}   ({ms:.0f} ms)")
        for i in scores.argsort()[::-1]:
            bar = "█" * max(0, int(scores[i] * 30))
            print(f"  {scores[i]:.3f}  {names[i]:<15} {bar}")

    # The prefix experiment: same question, same reels, but WITHOUT the "query:"/"passage:" labels.
    # Only meaningful for models that use labels (e5) — try: embedding.Embedder("e5-small").
    if not E.spec["query_prefix"]:
        return
    print("\nExperiment: no 'query:' / 'passage:' labels")
    raw = E._embed(list(REELS.values()))
    q = E._embed([QUESTIONS[0]])[0]
    for i in (raw @ q).argsort()[::-1]:
        print(f"  {float(raw[i] @ q):.3f}  {names[i]}")


if __name__ == "__main__":
    main()
