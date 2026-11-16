# substack-pangram-analysis

How much of what is published on Substack is written by AI, measured with
**Substack's own AI detector**: the Pangram integration behind the reader's
*Scan for AI text* menu.

This repository has the code to reproduce the study. It has no data: every
step collects its data from Substack's public endpoints into `./data/`, which
git ignores.

## What it measures

For every post it scans, the detector answers with the share of the text that
is human, AI-assisted and AI-generated, or says why it can't score the post:
the author turned detection off, the post is paywalled, too short, audio or
video, or published before detection started (21 July 2026, 15:30 UTC).

The study draws two independent samples of posts published in one period:

| Frame | What it stands for | How it is drawn |
|---|---|---|
| **Ranked** | The visible part of Substack: the top publications of each category | Every post each ranked publication published in the period is listed (the *census*). A share of each publication's posts is drawn at random. |
| **Random** | Substack as a whole, including publications nobody reads | Post IDs drawn uniformly from the ID range covering the period. Each is then dated and kept only if it was published in the period. |

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- No API keys. Every endpoint is public and unauthenticated.

```bash
uv sync
uv run pytest        # offline, a few seconds
```

## Reproducing the study

Run the six steps in order. Each one can be interrupted and re-run: it skips
what is already on disk.

```bash
# 1. The free category rankings, 500 deep (~15 min)
uv run substack-pangram rankings

# 2. Every post the ranked publications published in the period (~3 s per publication)
uv run substack-pangram census --end 2026-10-28T00:00:00+00:00
uv run substack-pangram census              # re-run to retry archives that failed
uv run substack-pangram census-summary      # how complete it is

# 3. Draw both frames into the worklist
uv run substack-pangram sample --share 0.8 --random-ids 12000

# 4. Scan every post against the detector (14,000-24,000 posts a day)
uv run substack-pangram scan
uv run substack-pangram scan --retry-errors # ask again about requests that never got an answer

# 5. Date the random frame's posts (one request per post that exists)
uv run substack-pangram date

# 6. The result tables, as Markdown
uv run substack-pangram report                       # each post counts once
uv run substack-pangram report --weight publication  # each publication counts once
```

`--start` (default: the detection start) and `--end` set the period on the
first `census` run. Every later step reads it from `data/period.json`.

Try it small first: `rankings --depth 5`, `census --limit 20`,
`sample --random-ids 50`, `scan --limit 30`.

### Sampling choices

- **`--share 0.8`** (default) draws 80% of each ranked publication's eligible
  posts, rounded up per publication. Every post has about the same chance of
  being drawn, so `report` describes ranked writing as a whole.
- **`--per-publication 3`** draws at most 3 posts per publication instead. It is
  much cheaper and gives each publication about the same weight.
- Either way, `report --weight publication` answers "what does a typical ranked
  publication look like", and `report --weight post` (the default) answers
  "what share of ranked posts".
- *Eligible* means free, at least 100 words, and not a podcast or video: a post
  the detector can score.
- The random frame's ID range is estimated from the census (see
  `src/substack_pangram/sample.py`). Pass `--band LO HI` to set it yourself.

## Output files (`data/`)

| File | One row per | Key fields |
|---|---|---|
| `publications.jsonl` | ranking appearance | `publication_id`, `domain`, `category`, `rank` (0 = top), `language` |
| `census.jsonl` | ranked publication | `posts` in the period (`id`, `post_date`, `audience`, `wordcount`, `type`), `reached_start` |
| `sample.json` | — | the draw's seed, share and random-frame ID range |
| `worklist.jsonl` | post to scan | `post_id`, `frames` (`ranked`, `random` or both), publication fields |
| `scans.jsonl` | scan attempt | `outcome`, `fraction_ai`, `fraction_ai_assisted`, `fraction_human`, `http_status` |
| `dates.jsonl` | random-frame lookup | `post_date`, `audience`, `wordcount`, `publication_id` |

The report's definitions:
- **Eligible** = `scored`, `disabled` or `paid`.
- **AI share** = `fraction_ai + fraction_ai_assisted`.
- A post drawn by both frames counts in both.
- Every interval is a 95% interval clustered by publication.

## Endpoints

| Step | Endpoint | Pace |
|---|---|---|
| rankings | `substack.com/api/v1/categories`, `/api/v1/category/public/<id>/all?page=N` | 1 request/s |
| census | `<publication>/api/v1/archive?sort=new&limit=25&cursor=…` | 2 requests/s |
| scan | `substack.com/api/v1/pangram/detection/p-<post_id>` | 1 request / 2.5 s |
| date | `substack.com/api/v1/posts/by-id/<post_id>` | 1 request / 2.5 s |

None of these is documented, and the limits above were measured, not
published. The detection endpoint answers 429 above about one request every
2.5 seconds, and the scanner backs off when it does. Please keep the pace:
running several scanners in parallel gets around the limit instead of
honouring it.

The archive endpoint changed in November 2026, from offset paging to cursor
paging with at most 25 posts per page. If Substack changes an endpoint again,
the step that uses it stops or records errors. It does not write partial
results that look complete.

## Things to know

- **`/all` is the free ranking.** It returns the same list as `/free`. It is
  not the ranking at `substack.com/leaderboard/<category>`, which is the paid
  (bestseller) ranking, so the publications won't match the website.
- **The census reflects today.** Posts paywalled or deleted since they were
  published are not eligible. In our run, about 8% of posts scored as free
  had been paywalled weeks later.
- **The detector is a black box.** Its accuracy, and its error rate outside
  English, are not measured here, and its model version is not exposed. To
  check that its verdicts haven't drifted over a long run, rescan a random
  sample of scored posts and compare.
- **Opt-outs are not random.** About one ranked post in seven has detection
  turned off, more often on long posts. Nothing in the data shows what those
  posts contain.
