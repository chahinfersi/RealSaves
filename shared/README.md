# RealSaves vector search (Phase 3)

The code both sides of vector search share: the **worker** embeds each reel once (write path),
the **MCP server** embeds the topic Claude searches for (read path). Both MUST use the same
model, so it lives here, once. Concepts: [`../VECTOR-SEARCH-EXPLAINED.md`](../VECTOR-SEARCH-EXPLAINED.md)
(and the clickable [`../vector-search-illustration.html`](../vector-search-illustration.html)).

| File | What it does |
|---|---|
| [`embedding.py`](embedding.py) | Text → vector. Runs the model locally with `onnxruntime` (no PyTorch). Each model is pinned to a commit |
| [`reels.py`](reels.py) | `passage_text(doc)`: the text that represents a reel (caption + note + transcript) |
| [`playground.py`](playground.py) | Embed a few sentences in Arabic / French / English and print the scores. Start here |
| [`evaluate.py`](evaluate.py) | Measure a model on **your** reels with a test set (read-only) |
| [`create_vector_index.py`](create_vector_index.py) | Create the Atlas Vector Search index (or do it in the Atlas UI) |

## Setup, in order

Dependencies come with the worker's (`onnxruntime`, `tokenizers`, `huggingface_hub`):
`pip install -r worker/requirements.txt`. The first run downloads the model once (bge-m3,
~2.3 GB, cached by Hugging Face).

```bash
python shared/playground.py                 # 1. see embeddings work, no database
python worker/embed_backfill.py --dry-run    # 2. how many ready reels need a vector
python worker/embed_backfill.py              #    embed them (~2 s per reel on CPU)
python shared/create_vector_index.py         # 3. create the Atlas index, wait for READY
```

Then restart Claude Desktop: the MCP server's `find_reels` tool uses the index
(see [`../mcp_server/README.md`](../mcp_server/README.md)).

**Creating the index in the Atlas UI instead:** Search & Vector Search → Create Search Index →
*Vector Search* → **Bring your own embeddings** (not "Automated Embedding", which is billed) →
database/collection `saves`, name `reels_vector` → JSON Editor:

```json
{ "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine" },
    { "type": "filter", "path": "owner" },
    { "type": "filter", "path": "status" },
    { "type": "filter", "path": "embeddingModel" }
] }
```

## Choosing a model: measure, don't guess

```bash
cp shared/eval_topics.example.json shared/eval_topics.json   # then fill it with YOUR reels
python shared/evaluate.py e5-small bge-m3
```

`eval_topics.json` is gitignored: it holds your own reel ids and topics. For each topic you
list the reels that *should* come back (and a couple of topics with no match, to test the
threshold). The script prints, per model, how many expected reels made the top 5 and the
similarity cut-off that best separates relevant from unrelated.

Our result on 76 real reels (English + Arabic dialects), 12 topics:

| Model | Dims | Found in top 5 | Relevant vs other (avg) | Best cut-off |
|---|---|---|---|---|
| `multilingual-e5-small` | 384 | 75% | 0.85 vs 0.80 | 0.86 (F1 0.51) |
| **`bge-m3`** ✅ | 1024 | **84%** | **0.58 vs 0.41** | 0.56 (F1 0.70) |

`find_reels` uses a slightly lower threshold (0.50): missing a good reel costs more than
showing a weak one, because Claude checks the results and asks the user before using them.

**Changing the model later** (or its revision in `MODELS`): it changes every vector, so update
`DEFAULT_MODEL`, re-run `worker/embed_backfill.py` (it re-embeds rows whose `embeddingModel`
differs), and recreate the index if the dimensions changed.
