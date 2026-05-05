"""Corpus loader.

The seed corpus lives at ``agent-service/src/copilot/rag/corpus/seed/``
as one JSON file per source organization (USPSTF, AAFP, ADA, ACIP).
Each file is a list of chunk objects:

.. code-block:: json

    [
      {
        "chunk_id": "uspstf-htn-2024-1",
        "title": "Screening for Hypertension in Adults",
        "section": "Recommendation",
        "source": "USPSTF",
        "source_url": "https://www.uspreventiveservicestaskforce.org/...",
        "year": 2024,
        "text": "The USPSTF recommends screening for hypertension..."
      },
      ...
    ]

Hand-curated, defensible to a clinical reviewer. We deliberately
keep the corpus small (target 30–50 chunks for the Sunday final);
size is not the differentiator, the rerank step is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_CORPUS_DIR = Path(__file__).parent / "corpus" / "seed"


@dataclass(frozen=True)
class Chunk:
    """One indexed unit of clinical guidance.

    ``chunk_id`` is the citation key referenced as ``Guideline#<chunk_id>``
    in the agent's response (verification regex enforces it). It must
    be globally unique across the corpus.
    """

    chunk_id: str
    title: str
    section: str
    source: str          # "USPSTF", "AAFP", "ADA", "ACIP", ...
    source_url: str
    year: int
    text: str

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id must be non-empty")
        if not self.text.strip():
            raise ValueError(f"chunk {self.chunk_id!r} has empty text")
        if "/" in self.chunk_id or " " in self.chunk_id:
            # Keep chunk_id usable inside our citation regex
            # (`Guideline#<chunk_id>`) — disallow chars the regex
            # would terminate on.
            raise ValueError(
                f"chunk_id {self.chunk_id!r} contains forbidden chars; "
                "must match [A-Za-z0-9._-]+"
            )


def load_corpus(corpus_dir: Path | None = None) -> list[Chunk]:
    """Read every ``*.json`` file under ``corpus_dir`` and return a
    flat list of :class:`Chunk`\\ s. Validates uniqueness of
    ``chunk_id`` across the whole corpus."""
    corpus_dir = corpus_dir or DEFAULT_CORPUS_DIR
    if not corpus_dir.exists():
        raise FileNotFoundError(f"corpus directory not found: {corpus_dir}")

    chunks: list[Chunk] = []
    for json_path in sorted(corpus_dir.glob("*.json")):
        with json_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list):
            raise ValueError(
                f"{json_path}: expected a JSON list of chunks, got "
                f"{type(raw).__name__}"
            )
        for entry in raw:
            chunks.append(Chunk(**entry))

    _check_unique_ids(chunks)
    return chunks


def _check_unique_ids(chunks: Iterable[Chunk]) -> None:
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise ValueError(
                f"duplicate chunk_id in corpus: {chunk.chunk_id!r}"
            )
        seen.add(chunk.chunk_id)
