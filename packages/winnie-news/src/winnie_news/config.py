"""Pipeline configuration.

The host owns the deployment paths and the enable switch; the stage settings
travel with the package so a CLI, the API and the tests configure the pipeline the
same way. Every value here has a default that is safe without a vendor.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from winnie_news.aggregate.store import AggregateConfig
from winnie_news.clean.cleaner import CleanConfig
from winnie_news.digest.models import DigestConfig
from winnie_news.generate.models import WritingRequirements
from winnie_news.review.reviewer import MAX_RETRIES
from winnie_news.search.search import SearchConfig


class MaterialFetchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty means "every source enabled in the shipped sources.json".
    source_types: list[str] = Field(default_factory=list)
    rss_feed_ids: list[str] = Field(default_factory=list)
    rss_limit: int | None = None
    # Relative paths resolve against the news root.
    cache_dir: str = "cache/material"
    max_age_seconds: float = 3600.0
    workers: int = 5
    # Optional overrides for the source registries; empty uses the packaged ones.
    sources_file: str = ""
    rss_sources_file: str = ""


class NewsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Relative to the API root when used by the host.
    root: str = "../../data/news"
    # The language the drafts are written in.
    language: str = "zh"
    material: MaterialFetchConfig = Field(default_factory=MaterialFetchConfig)
    clean: CleanConfig = Field(default_factory=CleanConfig)
    aggregate: AggregateConfig = Field(default_factory=AggregateConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    writing: WritingRequirements = Field(default_factory=WritingRequirements)
    # The batch briefing assembled from the drafts that passed review.
    digest: DigestConfig = Field(default_factory=DigestConfig)
    max_review_retries: int = MAX_RETRIES
    # Older runs are pruned after a successful run.
    keep_runs: int = 50
