"""Embeddings on the worker side (write path of vector search).

Turns ready reels into the two fields Atlas Vector Search will index:

  embedding       the reel's vector (list of floats, e.g. 1024 for bge-m3)
  embeddingModel  which model + revision made it — vectors from different models can't be
                  compared, so this is how we spot rows that need re-embedding

The model itself lives in ../shared/embedding.py, shared with the MCP server so both sides are
guaranteed to use the same one.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

# ../shared is a plain folder, not an installed package: put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

import embedding  # noqa: E402
from reels import passage_text  # noqa: E402

_embedder = None  # loaded on first use (~5 s), then reused for the whole run


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = embedding.Embedder()
    return _embedder


def current_model_id():
    return get_embedder().model_id


def embedding_fields(docs):
    """Return one {embedding, embeddingModel, embeddedAt} dict per doc, in the same order."""
    embedder = get_embedder()
    vectors = embedder.embed_passages([passage_text(d) for d in docs])
    now = datetime.now(timezone.utc)
    return [
        {"embedding": v.tolist(), "embeddingModel": embedder.model_id, "embeddedAt": now}
        for v in vectors
    ]
