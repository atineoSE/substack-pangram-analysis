"""Step 4: ask Substack's Pangram detector about every post in the worklist.

    GET https://substack.com/api/v1/pangram/detection/p-<post_id>

The same endpoint the reader's "Scan for AI text" menu calls. Every attempt is
recorded, scored or not, because why a post could not be scored is part of
the result. Paced at one request every ``SCAN_DELAY_SECONDS``: expect about
14,000-24,000 posts a day.

Outcomes:

=====================  ==========================================================
``scored``             ``type: success``; carries the three fractions
``disabled``           the author turned detection off for this post
``paid``               "Subscription required"
``too_short``          "Not enough text"
``unsupported_media``  "Video isn't supported yet"
``pre_cutoff``         "Not eligible for AI detection" (published before detection)
``dead``               404: no such public post
``error``              anything else; the header is kept so a change is visible
=====================  ==========================================================
"""

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from substack_pangram import http, store
from substack_pangram.config import SCAN_DELAY_SECONDS, DataDir

log = logging.getLogger(__name__)

DETECTION_URL = "https://substack.com/api/v1/pangram/detection/p-{post_id}"

ERROR_HEADERS = {
    "Not eligible for AI detection": "pre_cutoff",
    "Subscription required": "paid",
    "Not enough text": "too_short",
    "Video isn't supported yet": "unsupported_media",
}


def parse_detection(post_id: int, http_status: int, payload: Any) -> dict[str, Any]:
    """Reduce one detection response to a scan row (without ``frames`` or ``scanned_at``)."""
    row: dict[str, Any] = {
        "post_id": post_id,
        "outcome": "error",
        "fraction_ai": None,
        "fraction_ai_assisted": None,
        "fraction_human": None,
        "publication_id": None,
        "publication_language": None,
        "header": None,
        "http_status": http_status,
    }
    if http_status == 404:
        row["outcome"] = "dead"
        return row
    if http_status != 200 or not isinstance(payload, dict):
        return row

    disclosure = payload.get("disclosure")
    pub = disclosure.get("pub") if isinstance(disclosure, dict) else None
    if isinstance(pub, dict):
        row["publication_id"] = pub.get("id")
        row["publication_language"] = pub.get("language")

    kind = payload.get("type")
    if kind == "success":
        row["outcome"] = "scored"
        for field in ("fraction_ai", "fraction_ai_assisted", "fraction_human"):
            row[field] = payload.get(field)
    elif kind == "disabled":
        row["outcome"] = "disabled"
    elif kind == "error":
        header = payload.get("header")
        row["header"] = header
        row["outcome"] = ERROR_HEADERS.get(header, "error") if isinstance(header, str) else "error"
    return row


def scan_post(post_id: int) -> tuple[dict[str, Any], Any]:
    """Scan one post. Never raises; returns the row and the decoded payload (``None`` if none)."""
    response = http.get(DETECTION_URL.format(post_id=post_id), delay=SCAN_DELAY_SECONDS)
    if response is None:
        return parse_detection(post_id, 0, None), None
    try:
        payload = response.json() if response.status_code == 200 else None
    except ValueError:
        payload = None
    return parse_detection(post_id, response.status_code, payload), payload


def pending(data: DataDir, retry_errors: bool = False) -> list[dict[str, Any]]:
    """Worklist entries with no answer yet: never scanned, or (with ``retry_errors``) only a retryable failure."""
    latest = store.latest_by_post(store.read(data.scans_file))
    out = []
    for entry in store.read(data.worklist_file):
        row = latest.get(entry["post_id"])
        if row is None or (retry_errors and row["outcome"] == "error" and http.is_retryable(row["http_status"])):
            out.append(entry)
    return out


def run_scan(data: DataDir, limit: int | None = None, keep_raw: bool = False, retry_errors: bool = False) -> None:
    """Scan every pending post, appending each row as it is answered. Safe to interrupt and re-run."""
    todo = pending(data, retry_errors)
    if limit is not None:
        todo = todo[:limit]
    log.info("Scanning %d posts", len(todo))
    start = last = time.monotonic()
    counts: dict[str, int] = {}
    for done, entry in enumerate(todo, start=1):
        row, payload = scan_post(entry["post_id"])
        row["frames"] = entry["frames"]
        row["scanned_at"] = datetime.now(UTC).isoformat()
        store.append(data.scans_file, row)
        if keep_raw and payload is not None:
            path = data.raw_dir / f"{entry['post_id'] % 1000:03d}" / f"{entry['post_id']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1

        now = time.monotonic()
        if now - last >= 60 or done == len(todo):
            rate = done / max(now - start, 1e-9) * 3600
            eta = (len(todo) - done) / max(rate, 1e-9)
            print(f"[{done}/{len(todo)}] {rate:.0f}/h, eta {eta:.1f}h {counts}", flush=True)
            last = now
