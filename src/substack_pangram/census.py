"""Step 2: list every post each ranked publication published in the period.

    GET https://<domain>/api/v1/archive?sort=new&limit=25[&cursor=<nextCursor>]

The archive is newest first and paged by an opaque ``nextCursor``. Paging stops
at the first page that ends before the period start. One row per publication is
appended to ``census.jsonl``; a publication whose archive could not be read is
not written, so re-running the step retries it.

The archive reflects the posts *as they are now*: a post paywalled or deleted
since it was published is listed as paid, or not at all.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from substack_pangram import http, store
from substack_pangram.config import (
    ARCHIVE_DELAY_SECONDS,
    ARCHIVE_PAGE_SIZE,
    MEDIA_TYPES,
    MIN_WORDCOUNT,
    DataDir,
    Period,
    parse_date,
)
from substack_pangram.rankings import unique_publications

log = logging.getLogger(__name__)

# A safety cap on posts listed per publication; recorded per row when hit.
MAX_LISTED = 2000

POST_FIELDS = ("id", "post_date", "audience", "wordcount", "type")


def list_archive(domain: str, stop_before: str) -> list[dict[str, Any]] | None:
    """A publication's posts, newest first, back to the first page ending before ``stop_before``.

    Returns:
        The posts, or ``None`` if any page failed: a partial listing must not be
        recorded as a complete one.
    """
    cutoff = parse_date(stop_before)
    posts: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(posts) < MAX_LISTED:
        params: dict[str, Any] = {"sort": "new", "limit": ARCHIVE_PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        response = http.get(f"https://{domain}/api/v1/archive", params, delay=ARCHIVE_DELAY_SECONDS)
        if response is None or response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        if isinstance(body, dict):
            page, cursor = body.get("posts") or [], body.get("nextCursor") or None
        elif isinstance(body, list):  # the pre-November-2026 shape: one final page
            page, cursor = body, None
        else:
            return None
        if not page:
            break
        posts.extend(page)
        oldest = parse_date(page[-1].get("post_date"))
        if (cutoff is not None and oldest is not None and oldest < cutoff) or not cursor:
            break
    return posts


def census_row(publication: dict[str, Any], posts: list[dict[str, Any]], period: Period) -> dict[str, Any]:
    dates = [d for d in (parse_date(p.get("post_date")) for p in posts) if d is not None]
    oldest = min(dates) if dates else None
    return {
        "publication_id": publication["publication_id"],
        "domain": publication["domain"],
        "listed": len(posts),
        "oldest_post_date": oldest.isoformat() if oldest else None,
        # The listing provably covers the whole period only if it reaches before it.
        "reached_start": oldest is not None and oldest < parse_date(period.start),  # type: ignore[operator]
        "hit_cap": len(posts) >= MAX_LISTED,
        "fetched_at": datetime.now(UTC).isoformat(),
        "posts": [{k: p.get(k) for k in POST_FIELDS} for p in posts if period.contains(p.get("post_date"))],
    }


def run_census(data: DataDir, period: Period, limit: int | None = None) -> int:
    """List every not-yet-listed ranked publication's archive. Returns how many were recorded."""
    data.write_period(period)
    publications = unique_publications(data)
    if not publications:
        raise SystemExit(f"No {data.publications_file}: run 'substack-pangram rankings' first.")
    done = {row["publication_id"] for row in store.read(data.census_file)}
    pending = [p for p in publications if p["publication_id"] not in done]
    if limit is not None:
        pending = pending[:limit]

    log.info("Census: %d of %d publications listed; listing %d", len(done), len(publications), len(pending))
    recorded = unread = 0
    for index, publication in enumerate(pending, start=1):
        posts = list_archive(publication["domain"], period.start)
        if posts is None:
            unread += 1
        else:
            store.append(data.census_file, census_row(publication, posts, period))
            recorded += 1
        if index % 100 == 0 or index == len(pending):
            log.info("Census: [%d/%d] unread so far: %d", len(done) + index, len(publications), unread)
    return recorded


def is_eligible(post: dict[str, Any]) -> bool:
    """Free, at least ``MIN_WORDCOUNT`` words, and not audio or video: a post the detector can score."""
    wordcount = post.get("wordcount")
    return (
        post.get("audience") == "everyone"
        and isinstance(wordcount, int)
        and wordcount >= MIN_WORDCOUNT
        and post.get("type") not in MEDIA_TYPES
    )


def eligible_posts(data: DataDir) -> list[tuple[int, dict[str, Any]]]:
    """``(publication_id, post)`` for every eligible census post, each post once (a cross-post keeps the first)."""
    seen: set[int] = set()
    out = []
    for row in store.read(data.census_file):
        for post in row["posts"]:
            if post["id"] not in seen and is_eligible(post):
                seen.add(post["id"])
                out.append((row["publication_id"], post))
    return out


def summary(data: DataDir) -> dict[str, Any]:
    rows = store.read(data.census_file)
    publications = unique_publications(data)
    return {
        "ranked_publications": len(publications),
        "listed": len(rows),
        "not_reaching_period_start": sum(1 for r in rows if not r["reached_start"]),
        "eligible_posts": len(eligible_posts(data)),
    }


def print_summary(data: DataDir) -> None:
    print(json.dumps(summary(data), indent=2))
