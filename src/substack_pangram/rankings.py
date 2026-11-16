"""Step 1: walk Substack's free category rankings.

    GET https://substack.com/api/v1/categories
    GET https://substack.com/api/v1/category/public/<category_id>/all?page=N   (25 per page)

``/all`` returns the same ranking as ``/free``. It is *not* the ranking shown at
substack.com/leaderboard/<slug>, which is the paid (bestseller) ranking, so the
list will not match the website.

A 400 means the ranking has ended, which is how a shallow category finishes. A
429 that outlasts its retries means nothing was learned, so the walk stops with
an error instead of writing a truncated ranking that looks complete.
"""

import logging
from typing import Any

from substack_pangram import http, store
from substack_pangram.config import RANKING_DELAY_SECONDS, RANKING_DEPTH, DataDir

log = logging.getLogger(__name__)

CATEGORIES_URL = "https://substack.com/api/v1/categories"
CATEGORY_PAGE_URL = "https://substack.com/api/v1/category/public/{category_id}/all"


class RankingUnavailable(RuntimeError):
    """The ranking endpoint stayed throttled past every retry."""


def _get_json(url: str, params: dict[str, Any] | None = None) -> tuple[int | None, Any]:
    response = http.get(url, params, delay=RANKING_DELAY_SECONDS, rate_limit_attempts=5, rate_limit_backoff=30.0)
    if response is None:
        return None, None
    if response.status_code == 429:
        raise RankingUnavailable(f"Still rate limited on {url}. Wait a few minutes and re-run.")
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def domain_of(entry: dict[str, Any]) -> str | None:
    """The publication's host: ``base_url`` (custom domains included), else ``<subdomain>.substack.com``."""
    base_url = entry.get("base_url")
    if isinstance(base_url, str) and base_url:
        return base_url.split("://", 1)[-1].rstrip("/")
    subdomain = entry.get("subdomain")
    if isinstance(subdomain, str) and subdomain:
        return f"{subdomain}.substack.com"
    return None


def walk_category(category_id: int, category: str, depth: int = RANKING_DEPTH) -> list[dict[str, Any]]:
    """One category's ranking, rank 0 first, to ``depth`` or the end of the ranking."""
    rows: list[dict[str, Any]] = []
    rank = page = 0
    while rank < depth:
        status, payload = _get_json(CATEGORY_PAGE_URL.format(category_id=category_id), {"page": page})
        if status != 200 or not isinstance(payload, dict):
            break  # 400: the ranking ended
        entries = payload.get("publications") or []
        if not entries:
            break
        for entry in entries:
            if rank >= depth:
                break
            domain = domain_of(entry)
            if isinstance(entry.get("id"), int) and isinstance(entry.get("name"), str) and domain:
                language = entry.get("language")
                rows.append(
                    {
                        "publication_id": entry["id"],
                        "name": entry["name"],
                        "domain": domain,
                        "category": category,
                        "rank": rank,
                        "language": language if isinstance(language, str) else None,
                    }
                )
            rank += 1
        if not payload.get("more"):
            break
        page += 1
    return rows


def walk_rankings(data: DataDir, depth: int = RANKING_DEPTH) -> int:
    """Walk every leaderboard-enabled top-level category and write ``publications.jsonl``.

    A publication ranked in several categories gets one row per category.

    Returns:
        The number of rows written.
    """
    status, categories = _get_json(CATEGORIES_URL)
    if status != 200 or not isinstance(categories, list):
        raise RankingUnavailable(f"The categories endpoint answered {status}.")

    rows: list[dict[str, Any]] = []
    for category in categories:
        if not isinstance(category, dict) or category.get("leaderboard_enabled") is not True:
            continue
        if not isinstance(category.get("id"), int) or not isinstance(category.get("name"), str):
            continue
        walked = walk_category(category["id"], category["name"], depth)
        log.info("%s: %d publications", category["name"], len(walked))
        rows.extend(walked)

    store.write(data.publications_file, rows)
    log.info("Wrote %d ranking rows (%d publications)", len(rows), len({r["publication_id"] for r in rows}))
    return len(rows)


def unique_publications(data: DataDir) -> list[dict[str, Any]]:
    """One row per publication, keeping the first (category, rank) it was found at."""
    unique: dict[int, dict[str, Any]] = {}
    for row in store.read(data.publications_file):
        unique.setdefault(row["publication_id"], row)
    return list(unique.values())
