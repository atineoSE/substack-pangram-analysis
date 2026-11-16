"""Step 6: the result tables, as Markdown, recomputed from what is on disk.

Definitions:

* **Eligible**: ``scored``, ``disabled`` or ``paid`` -- a real post in the period
  that the detector could have scored. Every other outcome is excluded, not missing.
* **AI share** of a scored post: ``fraction_ai + fraction_ai_assisted``.
* A post drawn by both frames counts in both.
* A random-frame post counts only once ``date`` places it inside the period.

Every interval is a 95% interval **clustered by publication**: AI use is close
to constant within a publication, so a publication's posts are not independent
draws. It is the linearised (sandwich) variance of a ratio estimator over
publications, with the ``C / (C - 1)`` small-sample factor.

``--weight post`` (the default) counts each post once: with a ``--share``
sample that describes ranked writing as a whole. ``--weight publication``
gives each publication the same total weight: it describes the typical
ranked publication, however often it posts.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from math import sqrt
from typing import Any

from substack_pangram import store
from substack_pangram.config import DataDir, Period

ELIGIBLE = ("scored", "disabled", "paid")
EXCLUDED = ("dead", "unsupported_media", "pre_cutoff", "too_short", "error")
FRAMES = ("ranked", "random")
RANK_BANDS = ((0, 10), (10, 25), (25, 50), (50, 100), (100, 200), (200, 300), (300, 400), (400, 500))
LENGTH_BANDS: tuple[tuple[int, int | None], ...] = ((0, 300), (300, 1000), (1000, 3000), (3000, 6000), (6000, None))
MIN_GROUP_POSTS = 100


@dataclass(frozen=True)
class Post:
    post_id: int
    frames: tuple[str, ...]
    outcome: str
    ai: float | None
    cluster: int | str
    category: str | None
    rank: int | None
    language: str | None
    wordcount: int | None
    inside: bool | None  # random frame: dated inside the period; ranked frame: always True


def load_posts(data: DataDir, period: Period) -> list[Post]:
    worklist = {e["post_id"]: e for e in store.read(data.worklist_file)}
    dates = store.latest_by_post(store.read(data.dates_file))
    posts = []
    for post_id, row in store.latest_by_post(store.read(data.scans_file)).items():
        entry = worklist.get(post_id, {})
        dated = dates.get(post_id, {})
        ai = None
        if row["outcome"] == "scored" and row["fraction_ai"] is not None and row["fraction_ai_assisted"] is not None:
            ai = row["fraction_ai"] + row["fraction_ai_assisted"]
        publication = row.get("publication_id") or entry.get("publication_id") or dated.get("publication_id")
        language = row.get("publication_language") or entry.get("language")
        posts.append(
            Post(
                post_id=post_id,
                frames=tuple(row["frames"]),
                outcome=row["outcome"],
                ai=ai,
                cluster=int(publication) if publication is not None else f"post-{post_id}",
                category=entry.get("category"),
                rank=entry.get("rank"),
                language=language.split("-")[0].lower() if isinstance(language, str) else None,
                wordcount=entry.get("wordcount") or dated.get("wordcount"),
                inside=True if "ranked" in row["frames"] else period.contains(dated.get("post_date")),
            )
        )
    return posts


def counts_toward(post: Post, frame: str) -> bool:
    return frame in post.frames and (frame == "ranked" or post.inside is True)


Weigher = Callable[[list[Post]], list[float]]


def per_post(posts: list[Post]) -> list[float]:
    return [1.0] * len(posts)


def per_publication(posts: list[Post]) -> list[float]:
    sizes = Counter(p.cluster for p in posts)
    return [1.0 / sizes[p.cluster] for p in posts]


def estimate(
    posts: list[Post], value: Callable[[Post], float], weigh: Weigher, z: float = 1.96
) -> tuple[float, float, float]:
    """Weighted share or mean of ``value`` and its publication-clustered 95% interval, as percentages."""
    if not posts:
        return (0.0, 0.0, 0.0)
    totals: dict[int | str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for post, w in zip(posts, weigh(posts), strict=True):
        totals[post.cluster][0] += w * value(post)
        totals[post.cluster][1] += w
    n = sum(size for _, size in totals.values())
    p = sum(k for k, _ in totals.values()) / n
    c = len(totals)
    if c < 2:
        return (100 * p, 100 * p, 100 * p)
    half = z * sqrt(c / (c - 1) * sum((k - p * size) ** 2 for k, size in totals.values()) / (n * n))
    return (100 * p, 100 * max(p - half, 0.0), 100 * min(p + half, 1.0))


def fmt(est: tuple[float, float, float]) -> str:
    return f"{est[0]:.1f}% ({est[1]:.1f}-{est[2]:.1f})"


def scored(posts: list[Post], frame: str) -> list[Post]:
    return [p for p in posts if counts_toward(p, frame) and p.ai is not None]


def _sample_section(posts: list[Post]) -> str:
    rows = ["## The sample", "", "| | Ranked | Random |", "|---|---:|---:|"]
    rows.append("| Scanned | " + " | ".join(f"{sum(f in p.frames for p in posts):,}" for f in FRAMES) + " |")
    for outcome in EXCLUDED:
        rows.append(
            f"| `{outcome}` | "
            + " | ".join(f"{sum(f in p.frames and p.outcome == outcome for p in posts):,}" for f in FRAMES)
            + " |"
        )
    live = [p for p in posts if "random" in p.frames and p.outcome != "dead"]
    where = Counter("inside" if p.inside else ("undated" if p.inside is None else "outside") for p in live)
    rows += [
        "",
        f"Random frame: of {len(live):,} posts that exist, {where['inside']:,} are inside the period, "
        f"{where['outside']:,} outside it and {where['undated']:,} undated. Only those inside count below.",
    ]
    return "\n".join(rows)


def _eligibility_section(posts: list[Post], weigh: Weigher) -> str:
    rows = ["## What could be measured (share of eligible posts)", "", "| | Ranked | Random |", "|---|---:|---:|"]
    eligible = {f: [p for p in posts if counts_toward(p, f) and p.outcome in ELIGIBLE] for f in FRAMES}
    rows.append("| Eligible posts | " + " | ".join(f"{len(eligible[f]):,}" for f in FRAMES) + " |")
    for outcome, label in (("scored", "Scored"), ("disabled", "Detection disabled"), ("paid", "Paywalled")):
        cells = [fmt(estimate(eligible[f], lambda p, o=outcome: float(p.outcome == o), weigh)) for f in FRAMES]
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _ai_section(posts: list[Post], weigh: Weigher) -> str:
    rows = ["## How much AI (scored posts)", "", "| | Ranked | Random |", "|---|---:|---:|"]
    groups = {f: scored(posts, f) for f in FRAMES}
    rows.append("| Scored posts | " + " | ".join(f"{len(groups[f]):,}" for f in FRAMES) + " |")
    rows.append("| Publications | " + " | ".join(f"{len({p.cluster for p in groups[f]}):,}" for f in FRAMES) + " |")
    measures: tuple[tuple[str, Callable[[Post], float]], ...] = (
        ("Mean AI share of the text", lambda p: p.ai or 0.0),
        ("Posts >=50% AI", lambda p: float((p.ai or 0.0) >= 0.5)),
        ("Posts >=90% AI (essentially AI)", lambda p: float((p.ai or 0.0) >= 0.9)),
        ("Posts between 10% and 90% AI", lambda p: float(0.1 < (p.ai or 0.0) < 0.9)),
        ("Posts <=10% AI (essentially human)", lambda p: float((p.ai or 0.0) <= 0.1)),
    )
    for label, value in measures:
        rows.append(f"| {label} | " + " | ".join(fmt(estimate(groups[f], value, weigh)) for f in FRAMES) + " |")
    return "\n".join(rows)


def _grouped_section(title: str, groups: dict[str, list[Post]], weigh: Weigher, order: list[str] | None = None) -> str:
    rows = [title, "", "| | Scored posts | Publications | Mean AI share | >=50% AI |", "|---|---:|---:|---:|---:|"]
    keys = order or sorted(groups, key=lambda k: estimate(groups[k], lambda p: p.ai or 0.0, weigh)[0], reverse=True)
    for key in keys:
        members = groups.get(key, [])
        if len(members) < MIN_GROUP_POSTS and order is None:
            continue
        if not members:
            continue
        mean = estimate(members, lambda p: p.ai or 0.0, weigh)
        half = estimate(members, lambda p: float((p.ai or 0.0) >= 0.5), weigh)
        rows.append(f"| {key} | {len(members):,} | {len({p.cluster for p in members}):,} | {fmt(mean)} | {fmt(half)} |")
    return "\n".join(rows)


def _category_section(posts: list[Post], weigh: Weigher) -> str:
    groups: dict[str, list[Post]] = defaultdict(list)
    for post in scored(posts, "ranked"):
        if post.category:
            groups[post.category].append(post)
    return _grouped_section("## By category (ranked frame)", groups, weigh)


def _rank_section(posts: list[Post], weigh: Weigher) -> str:
    groups: dict[str, list[Post]] = defaultdict(list)
    labels = [f"{lo + 1}-{hi}" for lo, hi in RANK_BANDS]
    for post in scored(posts, "ranked"):
        for (lo, hi), label in zip(RANK_BANDS, labels, strict=True):
            if post.rank is not None and lo <= post.rank < hi:
                groups[label].append(post)
    groups["Random sample"] = scored(posts, "random")
    return _grouped_section(
        "## By rank in category (ranked frame; random for reference)", groups, weigh, [*labels, "Random sample"]
    )


def _language_section(posts: list[Post], weigh: Weigher) -> str:
    groups: dict[str, list[Post]] = defaultdict(list)
    for post in scored(posts, "ranked"):
        groups[post.language or "unknown"].append(post)
    return _grouped_section("## By the publication's declared language (ranked frame)", groups, weigh)


def _opt_out_section(posts: list[Post], weigh: Weigher) -> str:
    eligible = [p for p in posts if counts_toward(p, "ranked") and p.outcome in ELIGIBLE and p.wordcount is not None]
    rows = [
        "## Detection disabled, by length (ranked frame, eligible posts)",
        "",
        "| Words | Eligible posts | Disabled |",
        "|---|---:|---:|",
    ]
    for lo, hi in LENGTH_BANDS:
        band = [p for p in eligible if p.wordcount >= lo and (hi is None or p.wordcount < hi)]  # type: ignore[operator]
        if band:
            label = f"{lo:,}-{hi - 1:,}" if hi is not None else f"{lo:,}+"
            rows.append(
                f"| {label} | {len(band):,} | {fmt(estimate(band, lambda p: float(p.outcome == 'disabled'), weigh))} |"
            )
    return "\n".join(rows)


def render(data: DataDir, weight: str = "post") -> str:
    period = data.read_period()
    posts = load_posts(data, period)
    weigh: Weigher = per_publication if weight == "publication" else per_post
    header = [
        "# Substack posts and Pangram's AI detection",
        "",
        f"Posts published {period.start} .. {period.end}. Weighting: per {weight}. "
        "Intervals are 95%, clustered by publication.",
    ]
    sections = [
        "\n".join(header),
        _sample_section(posts),
        _eligibility_section(posts, weigh),
        _ai_section(posts, weigh),
        _category_section(posts, weigh),
        _rank_section(posts, weigh),
        _language_section(posts, weigh),
        _opt_out_section(posts, weigh),
    ]
    return "\n\n".join(sections) + "\n"


def summary(data: DataDir) -> dict[str, Any]:
    """A few headline numbers, for scripts."""
    posts = load_posts(data, data.read_period())
    return {f: {"scored": len(scored(posts, f))} for f in FRAMES}
