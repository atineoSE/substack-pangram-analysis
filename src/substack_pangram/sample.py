"""Step 3: choose the posts to scan, from two independent frames.

**Ranked frame.** From each ranked publication's eligible census posts, draw at
random either a share of them (``--share``, rounded up per publication) or at
most a fixed number (``--per-publication``). A share gives every post about the
same chance of being drawn, so per-post figures need no weights; a fixed
number gives every publication about the same weight.

**Random frame.** Post IDs are dense integers assigned when a draft is created,
so a uniform draw from the ID range covering the period is a uniform draw over
drafts started in it. Most IDs never became a public post, and some became
posts published outside the period; ``date`` settles which is which. The range
is estimated from the census: its upper edge is the highest ID published in the
period, its lower edge ``draft_lag`` IDs below the median ID published on the
period's first day, so drafts started before the period but published in it are
still in range.

Both frames are merged on post ID -- a post drawn by both is scanned once and
counts in both -- and the worklist is shuffled, so any prefix of it is a
uniform slice of the whole and a scan stopped early is still analysable.
"""

import json
import logging
import math
import random
import statistics
from collections import defaultdict
from datetime import timedelta
from typing import Any

from substack_pangram import store
from substack_pangram.census import eligible_posts
from substack_pangram.config import DEFAULT_DRAFT_LAG_IDS, DataDir, Period, parse_date
from substack_pangram.rankings import unique_publications

log = logging.getLogger(__name__)


def draw_ranked(
    eligible: list[tuple[int, dict[str, Any]]],
    rng: random.Random,
    *,
    share: float | None = None,
    per_publication: int | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """Draw from each publication's eligible posts: ``ceil(share * n)`` of them, or ``min(per_publication, n)``."""
    if (share is None) == (per_publication is None):
        raise ValueError("Give exactly one of share or per_publication.")
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for publication_id, post in eligible:
        groups[publication_id].append(post)

    chosen = []
    for publication_id in sorted(groups):  # sorted, so the draw does not depend on census order
        posts = sorted(groups[publication_id], key=lambda p: p["id"])
        k = math.ceil(share * len(posts) - 1e-9) if share is not None else min(per_publication or 0, len(posts))
        chosen.extend((publication_id, post) for post in rng.sample(posts, k=min(k, len(posts))))
    return chosen


def estimate_band(eligible: list[tuple[int, dict[str, Any]]], period: Period, draft_lag: int) -> tuple[int, int]:
    """The post-ID range the random frame draws from (see the module docstring)."""
    start = parse_date(period.start)
    assert start is not None
    first_day = [
        post["id"]
        for _, post in eligible
        if start <= parse_date(post["post_date"]) < start + timedelta(days=1)  # type: ignore[operator]
    ]
    if not first_day:
        raise SystemExit("No census posts on the period's first day to anchor the random frame; pass --band.")
    lo = int(statistics.median(first_day)) - draft_lag
    hi = max(post["id"] for _, post in eligible)
    return lo, hi


def build_worklist(
    data: DataDir,
    *,
    seed: int,
    share: float | None,
    per_publication: int | None,
    random_ids: int,
    band: tuple[int, int] | None = None,
    draft_lag: int = DEFAULT_DRAFT_LAG_IDS,
) -> list[dict[str, Any]]:
    """Draw both frames and write ``worklist.jsonl``. Refuses to overwrite a worklist that has scans."""
    if data.scans_file.exists() and data.scans_file.stat().st_size > 0:
        raise SystemExit(f"{data.scans_file} already has scans; re-drawing now would orphan them.")
    period = data.read_period()
    publications = {p["publication_id"]: p for p in unique_publications(data)}
    eligible = eligible_posts(data)
    rng = random.Random(seed)

    entries: dict[int, dict[str, Any]] = {}
    for publication_id, post in draw_ranked(eligible, rng, share=share, per_publication=per_publication):
        publication = publications[publication_id]
        entries[post["id"]] = {
            "post_id": post["id"],
            "frames": ["ranked"],
            "publication_id": publication_id,
            "domain": publication["domain"],
            "category": publication["category"],
            "rank": publication["rank"],
            "language": publication["language"],
            "post_date": post["post_date"],
            "wordcount": post["wordcount"],
        }
    ranked = len(entries)

    lo, hi = band if band is not None else estimate_band(eligible, period, draft_lag)
    for post_id in rng.sample(range(lo, hi + 1), k=min(random_ids, hi - lo + 1)):
        if post_id in entries:
            entries[post_id]["frames"].append("random")
        else:
            entries[post_id] = {"post_id": post_id, "frames": ["random"]}

    worklist = list(entries.values())
    rng.shuffle(worklist)
    store.write(data.worklist_file, worklist)
    # The draw's parameters, so a worklist can always be traced back to how it was made.
    (data.root / "sample.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "share": share,
                "per_publication": per_publication,
                "random_ids": random_ids,
                "band": [lo, hi],
                "eligible_posts": len(eligible),
                "ranked_drawn": ranked,
            },
            indent=2,
        )
        + "\n"
    )
    log.info(
        "Ranked frame: %d of %d eligible posts from %d publications. Random frame: %d IDs from [%d, %d]. "
        "Worklist: %d posts.",
        ranked,
        len(eligible),
        len({pid for pid, _ in eligible}),
        random_ids,
        lo,
        hi,
        len(worklist),
    )
    return worklist
