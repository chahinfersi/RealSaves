"""Create (once) the Atlas Vector Search index that find_reels queries.

    python shared/create_vector_index.py           # create it, then wait until it's READY
    python shared/create_vector_index.py --status  # just show the index state

What the index holds (../VECTOR-SEARCH-EXPLAINED.md §5.1):
  - `embedding`         the vectors, organised as an HNSW "highway map", compared by cosine
  - `owner`, `status`   copied in as FILTER fields, so the pre-filter runs inside the index

Atlas builds and syncs it by itself from the `saves` collection — we never write to it directly.
Free M0 clusters allow at most 3 search/vector indexes in total; this uses one.
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

import embedding

INDEX_NAME = "reels_vector"  # find_reels refers to the index by this name


def definition():
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": embedding.MODELS[embedding.DEFAULT_MODEL]["dimensions"],
                "similarity": "cosine",
            },
            {"type": "filter", "path": "owner"},
            {"type": "filter", "path": "status"},
            # Only vectors from the CURRENT model may be compared with the query vector.
            {"type": "filter", "path": "embeddingModel"},
        ]
    }


def find_index(collection):
    return next((i for i in collection.list_search_indexes() if i["name"] == INDEX_NAME), None)


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    collection = MongoClient(os.environ["MONGODB_URI"])[os.environ["DB_NAME"]]["saves"]

    index = find_index(collection)
    if "--status" in sys.argv:
        print(index or f"no index named {INDEX_NAME!r}")
        return
    if index is None:
        collection.create_search_index(
            SearchIndexModel(definition=definition(), name=INDEX_NAME, type="vectorSearch")
        )
        print(f"[index] {INDEX_NAME!r} requested — Atlas is building it…")
    else:
        print(f"[index] {INDEX_NAME!r} already exists")

    while True:  # building takes from a few seconds to a minute or two
        index = find_index(collection)
        state = index.get("status") if index else "missing"
        print(f"[index] status: {state}")
        if state in ("READY", "FAILED"):
            break
        time.sleep(5)
    if state == "FAILED":
        print(index)


if __name__ == "__main__":
    main()
