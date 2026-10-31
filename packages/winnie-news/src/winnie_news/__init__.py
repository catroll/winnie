"""News production pipeline for Winnie.

The pipeline turns many news sources into a publishable, traceable draft in six
stages:

    material -> clean -> aggregate -> search -> generate -> review
                                                   ^          |
                                                   +- retry --+

Each stage lives in its own subpackage and exchanges plain JSON-shaped data so a
stage can be run alone, replayed from disk, or tested without the others. The
schemas are versioned strings such as ``winnie.news.material/v1``; see
``docs/news.md`` in the repository for the contracts and the status of each
stage.
"""

from __future__ import annotations

__version__ = "0.1.0"

MATERIAL_SCHEMA = "winnie.news.material/v1"
CLEAN_SCHEMA = "winnie.news.clean/v1"
EVENT_SCHEMA = "winnie.news.event/v1"
SEARCH_SCHEMA = "winnie.news.search/v1"
DRAFT_SCHEMA = "winnie.news.draft/v1"
REVIEW_SCHEMA = "winnie.news.review/v1"

__all__ = [
    "CLEAN_SCHEMA",
    "DRAFT_SCHEMA",
    "EVENT_SCHEMA",
    "MATERIAL_SCHEMA",
    "REVIEW_SCHEMA",
    "SEARCH_SCHEMA",
    "__version__",
]
