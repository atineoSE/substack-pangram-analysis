"""The command line: six steps, run in order, each resumable.

substack-pangram rankings
substack-pangram census --end 2026-10-28T00:00:00+00:00
substack-pangram sample --share 0.8
substack-pangram scan
substack-pangram date
substack-pangram report
"""

import argparse
import logging
from pathlib import Path

from substack_pangram.census import print_summary, run_census
from substack_pangram.config import DEFAULT_RANDOM_IDS, DEFAULT_SHARE, PANGRAM_START, RANKING_DEPTH, DataDir, Period
from substack_pangram.date import run_dating
from substack_pangram.rankings import walk_rankings
from substack_pangram.report import render
from substack_pangram.sample import build_worklist
from substack_pangram.scan import run_scan


def main() -> None:
    parser = argparse.ArgumentParser(prog="substack-pangram", description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, default=Path("data"), help="Data directory (default: ./data)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="step", required=True)

    p = sub.add_parser("rankings", help="Walk the free category rankings")
    p.add_argument("--depth", type=int, default=RANKING_DEPTH)

    p = sub.add_parser("census", help="List every ranked publication's posts in the period (resumable)")
    p.add_argument("--start", help=f"Period start, ISO-8601 (default: {PANGRAM_START})")
    p.add_argument("--end", help="Period end, ISO-8601, exclusive. Required on the first run; later runs reuse it")
    p.add_argument("--limit", type=int, help="List at most this many publications (trial run)")
    sub.add_parser("census-summary", help="Print how complete the census is")

    p = sub.add_parser("sample", help="Draw the ranked and random frames into the worklist")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--share", type=float, help=f"Share of each publication's posts (default: {DEFAULT_SHARE})")
    group.add_argument("--per-publication", type=int, help="At most this many posts per publication instead")
    p.add_argument("--random-ids", type=int, default=DEFAULT_RANDOM_IDS)
    p.add_argument("--band", type=int, nargs=2, metavar=("LO", "HI"), help="Random-frame ID range (default: estimated)")
    p.add_argument("--seed", type=int, default=20260721)

    p = sub.add_parser("scan", help="Scan the worklist against the detector (resumable)")
    p.add_argument("--limit", type=int)
    p.add_argument("--raw", action="store_true", help="Also keep each raw response under data/raw/")
    p.add_argument("--retry-errors", action="store_true", help="Ask again about requests that never got an answer")

    p = sub.add_parser("date", help="Date the random frame's posts (after scan)")
    p.add_argument("--limit", type=int)

    p = sub.add_parser("report", help="Print the result tables as Markdown")
    p.add_argument("--weight", choices=("post", "publication"), default="post")

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    data = DataDir(args.data)

    if args.step == "rankings":
        walk_rankings(data, args.depth)
    elif args.step == "census":
        run_census(data, _census_period(data, args.start, args.end), args.limit)
    elif args.step == "census-summary":
        print_summary(data)
    elif args.step == "sample":
        share = args.share if args.share is not None or args.per_publication is not None else DEFAULT_SHARE
        build_worklist(
            data,
            seed=args.seed,
            share=share,
            per_publication=args.per_publication,
            random_ids=args.random_ids,
            band=tuple(args.band) if args.band else None,
        )
    elif args.step == "scan":
        run_scan(data, args.limit, keep_raw=args.raw, retry_errors=args.retry_errors)
    elif args.step == "date":
        run_dating(data, args.limit)
    elif args.step == "report":
        print(render(data, args.weight))


def _census_period(data: DataDir, start: str | None, end: str | None) -> Period:
    """The period from the flags on a first run; on a re-run, the saved one, which flags may not change."""
    if data.period_file.exists():
        saved = data.read_period()
        if (start and start != saved.start) or (end and end != saved.end):
            raise SystemExit(f"{data.period_file} already sets {saved.start} .. {saved.end}; use a new --data dir.")
        return saved
    if end is None:
        raise SystemExit("The first census run needs --end.")
    return Period(start=start or PANGRAM_START, end=end)


if __name__ == "__main__":
    main()
