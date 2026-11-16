"""Step 5: date the random frame's posts.

    GET https://substack.com/api/v1/posts/by-id/<post_id>

A random draw is an ID and nothing else, and the detector never says when a
post was published. A random-frame post counts only once this lookup places it
inside the period. Ranked-frame posts need no lookup: the census dated them.
Runs after ``scan``, over the posts the scan found to exist.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from substack_pangram import http, store
from substack_pangram.config import SCAN_DELAY_SECONDS, DataDir

log = logging.getLogger(__name__)

BY_ID_URL = "https://substack.com/api/v1/posts/by-id/{post_id}"


def parse_post(post_id: int, http_status: int, payload: Any) -> dict[str, Any]:
    post = payload.get("post") if isinstance(payload, dict) else None
    post = post if isinstance(post, dict) else {}

    def typed(key: str, kind: type) -> Any:
        value = post.get(key)
        return value if isinstance(value, kind) else None

    return {
        "post_id": post_id,
        "http_status": http_status,
        "post_date": typed("post_date", str),
        "type": typed("type", str),
        "audience": typed("audience", str),
        "wordcount": typed("wordcount", int),
        "publication_id": typed("publication_id", int),
        "dated_at": datetime.now(UTC).isoformat(),
    }


def pending(data: DataDir) -> list[int]:
    """Random-frame posts that exist and have no settled date yet."""
    dates = store.latest_by_post(store.read(data.dates_file))
    scans = store.latest_by_post(store.read(data.scans_file))
    return [
        post_id
        for post_id, row in scans.items()
        if "random" in row["frames"]
        and row["outcome"] != "dead"
        and (post_id not in dates or http.is_retryable(dates[post_id]["http_status"]))
    ]


def run_dating(data: DataDir, limit: int | None = None) -> None:
    period = data.read_period()
    todo = pending(data)
    if limit is not None:
        todo = todo[:limit]
    inside = 0
    for done, post_id in enumerate(todo, start=1):
        response = http.get(BY_ID_URL.format(post_id=post_id), delay=SCAN_DELAY_SECONDS)
        status = 0 if response is None else response.status_code
        try:
            payload = response.json() if response is not None and status == 200 else None
        except ValueError:
            payload = None
        row = parse_post(post_id, status, payload)
        store.append(data.dates_file, row)
        inside += bool(period.contains(row["post_date"]))
        if done % 100 == 0 or done == len(todo):
            print(f"[{done}/{len(todo)}] inside the period so far: {inside}", flush=True)
