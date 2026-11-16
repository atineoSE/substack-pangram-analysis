"""Study constants and the on-disk layout. Every stage reads its parameters from here."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Substack scans posts published from this instant onward (2026-07-21 17:30 CEST).
# Earlier posts answer "Not eligible for AI detection".
PANGRAM_START = "2026-07-21T15:30:00+00:00"

# How deep each category ranking is walked.
RANKING_DEPTH = 500

# The detector answers "Not enough text" below this many words.
MIN_WORDCOUNT = 100

# Post types the detector cannot score ("Video isn't supported yet").
MEDIA_TYPES = frozenset({"podcast", "video"})

# Seconds between detection requests. The endpoint publishes no limit; it starts
# answering 429 above roughly one request every 2.5 s.
SCAN_DELAY_SECONDS = 2.5

# Seconds between archive requests, and between ranking requests (the ranking
# endpoint throttles at the archive's pace).
ARCHIVE_DELAY_SECONDS = 0.5
RANKING_DELAY_SECONDS = 1.0

# The archive endpoint's largest accepted page.
ARCHIVE_PAGE_SIZE = 25

# Default share of each ranked publication's eligible posts to sample.
DEFAULT_SHARE = 0.8

# Default number of post IDs the random frame draws.
DEFAULT_RANDOM_IDS = 12_000

# A post ID is assigned when its draft is created, not when it is published, so
# a post published at the period start can carry an ID well below that day's
# newest IDs. The random frame's lower edge sits this many IDs below the start
# day's median ID; measured in 2026 as covering ~95% of posts.
DEFAULT_DRAFT_LAG_IDS = 4_160_000

USER_AGENT = "Mozilla/5.0 (compatible; substack-pangram-analysis/0.1)"
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Period:
    """The half-open publication period ``[start, end)``, ISO-8601 UTC."""

    start: str
    end: str

    def contains(self, post_date: str | None) -> bool | None:
        """Whether ``post_date`` is inside the period; ``None`` when it is missing or unparseable."""
        posted = parse_date(post_date)
        if posted is None:
            return None
        return parse_date(self.start) <= posted < parse_date(self.end)  # type: ignore[operator]


def parse_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, accepting Substack's trailing ``Z``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class DataDir:
    """Where every stage reads and writes. Nothing outside it is written."""

    root: Path

    @property
    def period_file(self) -> Path:
        return self.root / "period.json"

    @property
    def publications_file(self) -> Path:
        return self.root / "publications.jsonl"

    @property
    def census_file(self) -> Path:
        return self.root / "census.jsonl"

    @property
    def worklist_file(self) -> Path:
        return self.root / "worklist.jsonl"

    @property
    def scans_file(self) -> Path:
        return self.root / "scans.jsonl"

    @property
    def dates_file(self) -> Path:
        return self.root / "dates.jsonl"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    def write_period(self, period: Period) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.period_file.write_text(json.dumps({"start": period.start, "end": period.end}) + "\n")

    def read_period(self) -> Period:
        if not self.period_file.exists():
            raise SystemExit(f"No {self.period_file}: run 'substack-pangram census --end ...' first.")
        data = json.loads(self.period_file.read_text())
        return Period(start=data["start"], end=data["end"])
