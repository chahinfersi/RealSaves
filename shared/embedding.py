"""Text → vector (an "embedding"), shared by BOTH sides of vector search:

  - the worker   (write path): embeds each reel once, after transcription
  - the MCP server (read path): embeds the topic Claude searches for

THE GOLDEN RULE (../VECTOR-SEARCH-EXPLAINED.md §5): both sides must use the exact same model,
or the vectors live on two different "maps" and their scores mean nothing. That's why this
is ONE file both import, and why each model is pinned to a commit (not "latest").

How it works, in 4 steps (no PyTorch needed — onnxruntime + tokenizers came with faster-whisper):

  1. tokenize   "passage: hello world" → token ids [0, 46692, 12, 33600, 31, 8999, 2]
  2. run model  each token → N numbers that depend on the whole sentence (last_hidden_state)
  3. pool       turn the token vectors into ONE vector for the whole text
                ("mean": average them all · "cls": keep the first, summary token)
  4. normalize  scale it to length 1, so cosine similarity = a plain dot product

Never print() here: the MCP server imports this module, and its stdout is the protocol channel.
"""

import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer

# Everything that makes a model's vectors comparable lives in its spec. Changing the model used
# for storage (or its revision) = new vectors = re-run the embedding backfill.
MODELS = {
    "e5-small": {
        "repo": "intfloat/multilingual-e5-small",
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "dimensions": 384,
        "max_tokens": 512,  # anything longer is cut off (silently, at the end)
        "pooling": "mean",
        # e5 was trained with these labels. Forget them and nothing crashes — results are
        # just quietly worse.
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    },
    "bge-m3": {
        "repo": "BAAI/bge-m3",
        "revision": "5617a9f61b028005a4858fdac845db406aefb181",
        "dimensions": 1024,
        # The model accepts 8192, but attention memory grows with length² — one 7k-token
        # transcript asked for 25 GB. 2048 tokens ≈ 10 minutes of speech, plenty for a reel.
        "max_tokens": 2048,
        "pooling": "cls",
        "query_prefix": "",  # bge-m3 needs no labels
        "passage_prefix": "",
    },
}
# Chosen by measurement (shared/evaluate.py, 76 real reels, 12 topics, 2026-10-08):
#   e5-small: 75% found in top 5, poor separation (F1 0.51)
#   bge-m3:   84% found in top 5, clear separation (F1 0.70), no-match topics stay below 0.45
DEFAULT_MODEL = "bge-m3"


class Embedder:
    """One loaded model. Loading is lazy: the download/load happens on first use."""

    def __init__(self, name=DEFAULT_MODEL):
        self.name = name
        self.spec = MODELS[name]
        self.model_id = f"{self.spec['repo']}@{self.spec['revision'][:8]}"  # stored next to vectors
        self._session = None
        self._tokenizer = None
        self._lock = threading.Lock()  # the MCP server warms the model up in a background thread

    def _load(self):
        with self._lock:
            if self._session is None:
                self._load_unlocked()

    def _load_unlocked(self):
        download = dict(repo_id=self.spec["repo"], revision=self.spec["revision"], allow_patterns=["onnx/*"])
        try:
            # Already downloaded? Use the cache without touching the network (works offline).
            folder = Path(snapshot_download(**download, local_files_only=True)) / "onnx"
        except Exception:
            # First time only: download the onnx/ folder (cached by Hugging Face afterwards).
            folder = Path(snapshot_download(**download)) / "onnx"

        tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=self.spec["max_tokens"])
        # Several texts in one batch must have the same length → pad the short ones.
        # The attention mask (step 3) makes sure padding never counts.
        tokenizer.enable_padding(pad_id=tokenizer.token_to_id("<pad>"), pad_token="<pad>")

        self._session = ort.InferenceSession(str(folder / "model.onnx"), providers=["CPUExecutionProvider"])
        self._tokenizer = tokenizer

    def _embed(self, texts):
        self._load()
        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        # Feed only the inputs this ONNX file declares (some exports also want token_type_ids).
        available = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        feed = {i.name: available[i.name] for i in self._session.get_inputs()}
        token_vectors = self._session.run(None, feed)[0]  # shape: (texts, tokens, dimensions)

        if self.spec["pooling"] == "cls":
            pooled = token_vectors[:, 0, :].copy()  # the first token is trained to summarize the text
        else:
            # Mean pooling: average the real tokens (mask = 1), ignoring padding (mask = 0).
            weights = mask[:, :, None].astype(np.float32)
            pooled = (token_vectors * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)

        pooled /= np.linalg.norm(pooled, axis=1, keepdims=True)  # length 1
        return pooled

    def embed_passages(self, texts, batch_size=4):
        """Embed texts we STORE (reels). Returns a (len(texts), dimensions) numpy array."""
        texts = [self.spec["passage_prefix"] + t for t in texts]
        # Padding makes every text in a batch as long as the longest one, so batch texts of
        # similar length together (sorted), then put the results back in the original order.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        result = np.zeros((len(texts), self.spec["dimensions"]), dtype=np.float32)
        for start in range(0, len(order), batch_size):
            chunk = order[start:start + batch_size]
            result[chunk] = self._embed([texts[i] for i in chunk])
        return result

    def embed_query(self, text):
        """Embed one search topic. Returns a numpy array of `dimensions` numbers."""
        return self._embed([self.spec["query_prefix"] + text])[0]

    def count_tokens(self, text):
        """How many tokens the model sees for this text (capped at max_tokens) — spots truncation."""
        self._load()
        return len(self._tokenizer.encode(self.spec["passage_prefix"] + text).ids)
