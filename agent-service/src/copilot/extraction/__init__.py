"""W2 multimodal extraction pipeline.

Surface:

- :func:`extract_words` — render a PDF to a normalized list of
  :class:`Word`\\ s using pdfplumber. This is the *ground truth*
  for citation bboxes; the vision model never decides coordinates.
- :func:`match_quote` — given a quote string the vision model
  emitted (e.g. ``"HDL Cholesterol 52 mg/dL"``) and the word list
  for the page the model claims it came from, find the smallest
  word span whose concatenated text best matches the quote, and
  return a :class:`BBox` over it.

The two together let the extraction pipeline (a) accept a strict
Pydantic schema from the vision pass, and (b) attach verifiable
bbox coordinates to every extracted fact. Fields whose quote
doesn't match anything on the claimed page are flagged
``extraction_confidence='low'`` and surfaced as warnings, never
written to the chart silently.
"""

from copilot.extraction.matcher import MatchResult, match_quote
from copilot.extraction.pdf import Word, extract_words, page_count

__all__ = ["MatchResult", "Word", "extract_words", "match_quote", "page_count"]
