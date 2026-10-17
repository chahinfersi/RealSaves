"""What text represents a reel for vector search (decision V2 in ../VECTOR-SEARCH-EXPLAINED.md).

One function, used by the evaluation, the worker and the backfill, so every reel is embedded
from the same recipe.
"""

# Invisible Unicode direction marks wrapped around some Arabic captions (same list as
# mcp_server/server.py). They carry no meaning, only noise.
_EDGE_JUNK = " \t\r\n‎‏⁦⁧⁨⁩"


def passage_text(doc):
    """caption + the user's note + transcript, most important first.

    Order matters: if the text is longer than the model's input limit, the END is cut off,
    so the short, dense parts (caption, note) go first and the long transcript last.
    """
    caption = (doc.get("caption") or doc.get("title") or "").strip(_EDGE_JUNK)
    parts = [
        " ".join(caption.split()),
        f"My note: {doc['note']}" if doc.get("note") else "",
        " ".join((doc.get("transcript") or "").split()),
    ]
    return "\n".join(p for p in parts if p)
