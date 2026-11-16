"""Offline tests: every network call is faked, so these run in seconds with no API access."""

import json
import random

import pytest

from substack_pangram import census, http, rankings, report, sample, scan, store
from substack_pangram.config import DataDir, Period

PERIOD = Period(start="2026-07-21T15:30:00+00:00", end="2026-10-28T00:00:00+00:00")


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


@pytest.fixture
def data(tmp_path):
    return DataDir(tmp_path / "data")


def _post(id, date="2026-08-01T00:00:00Z", audience="everyone", wordcount=500, type="newsletter"):
    return {"id": id, "post_date": date, "audience": audience, "wordcount": wordcount, "type": type}


# --- http -------------------------------------------------------------------


def test_get_retries_a_429_then_returns_the_answer(monkeypatch):
    answers = [FakeResponse(429), FakeResponse(200, {"ok": 1})]
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: answers.pop(0))

    assert http.get("u", delay=0).json() == {"ok": 1}


def test_get_returns_a_404_on_the_first_try(monkeypatch):
    calls = []
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(404))

    assert http.get("u", delay=0).status_code == 404
    assert len(calls) == 1


# --- rankings ---------------------------------------------------------------


def test_a_category_walk_stops_at_the_400_that_ends_the_ranking(monkeypatch):
    pages = {
        0: FakeResponse(
            200, {"publications": [{"id": i, "name": f"P{i}", "subdomain": f"p{i}"} for i in range(25)], "more": True}
        ),
        1: FakeResponse(400, {"errors": []}),
    }
    monkeypatch.setattr(http.requests, "get", lambda url, params=None, **k: pages[params["page"]])

    rows = rankings.walk_category(1, "News")

    assert [r["rank"] for r in rows] == list(range(25))
    assert rows[0]["domain"] == "p0.substack.com"


def test_a_throttled_ranking_fails_loudly(monkeypatch):
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: FakeResponse(429))

    with pytest.raises(rankings.RankingUnavailable):
        rankings.walk_category(1, "News")


# --- census -----------------------------------------------------------------


def test_the_archive_is_paged_by_cursor_until_a_page_ends_before_the_start(monkeypatch):
    pages = {
        None: {"posts": [_post(3, "2026-09-01T00:00:00Z")], "nextCursor": "a"},
        "a": {"posts": [_post(2, "2026-08-01T00:00:00Z"), _post(1, "2026-07-01T00:00:00Z")], "nextCursor": "b"},
    }
    seen = []

    def fake_get(url, params=None, **k):
        seen.append(params.get("cursor"))
        assert "offset" not in params and params["limit"] <= 25
        return FakeResponse(200, pages[params.get("cursor")])

    monkeypatch.setattr(http.requests, "get", fake_get)

    posts = census.list_archive("x.com", PERIOD.start)

    assert [p["id"] for p in posts] == [3, 2, 1]
    assert seen == [None, "a"]


def test_a_failed_page_means_no_listing_at_all(monkeypatch):
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: FakeResponse(503))

    assert census.list_archive("x.com", PERIOD.start) is None


def test_census_keeps_period_posts_and_retries_unread_publications(data, monkeypatch):
    store.write(
        data.publications_file,
        [
            {"publication_id": 1, "name": "A", "domain": "a.com", "category": "News", "rank": 0, "language": "en"},
            {"publication_id": 2, "name": "B", "domain": "b.com", "category": "News", "rank": 1, "language": "en"},
        ],
    )
    listings = {"a.com": [_post(1), _post(2, "2026-07-01T00:00:00Z")], "b.com": None}
    monkeypatch.setattr(census, "list_archive", lambda domain, stop: listings[domain])

    assert census.run_census(data, PERIOD) == 1
    row = store.read(data.census_file)[0]
    assert [p["id"] for p in row["posts"]] == [1]
    assert row["reached_start"] is True

    listings["b.com"] = [_post(5)]
    assert census.run_census(data, PERIOD) == 1  # only the publication that failed is listed again


def test_eligibility_excludes_paid_short_and_media_posts():
    assert census.is_eligible(_post(1))
    assert not census.is_eligible(_post(1, audience="only_paid"))
    assert not census.is_eligible(_post(1, wordcount=99))
    assert not census.is_eligible(_post(1, type="podcast"))


# --- sample -----------------------------------------------------------------


def test_a_share_rounds_up_per_publication():
    eligible = [(1, _post(i)) for i in range(10)] + [(2, _post(100 + i)) for i in range(3)]

    chosen = sample.draw_ranked(eligible, random.Random(0), share=0.8)

    by_publication = {pid: sum(1 for p, _ in chosen if p == pid) for pid in (1, 2)}
    assert by_publication == {1: 8, 2: 3}


def test_a_fixed_number_per_publication_never_pads():
    eligible = [(1, _post(i)) for i in range(10)] + [(2, _post(100))]

    chosen = sample.draw_ranked(eligible, random.Random(0), per_publication=3)

    assert sorted(pid for pid, _ in chosen) == [1, 1, 1, 2]


def test_the_draw_does_not_depend_on_census_order():
    eligible = [(1, _post(i)) for i in range(20)]

    a = sample.draw_ranked(eligible, random.Random(7), share=0.5)
    b = sample.draw_ranked(list(reversed(eligible)), random.Random(7), share=0.5)

    assert [p["id"] for _, p in a] == [p["id"] for _, p in b]


def test_the_band_reaches_below_the_first_day_by_the_draft_lag():
    eligible = [
        (1, _post(1000, "2026-07-21T16:00:00Z")),
        (1, _post(1002, "2026-07-21T20:00:00Z")),
        (1, _post(5000, "2026-10-27T00:00:00Z")),
    ]

    assert sample.estimate_band(eligible, PERIOD, draft_lag=100) == (901, 5000)


def test_worklist_merges_frames_on_post_id(data):
    data.write_period(PERIOD)
    store.write(
        data.publications_file,
        [{"publication_id": 1, "name": "A", "domain": "a.com", "category": "News", "rank": 0, "language": "en"}],
    )
    store.write(
        data.census_file, [{"publication_id": 1, "listed": 2, "reached_start": True, "posts": [_post(10), _post(11)]}]
    )

    worklist = sample.build_worklist(data, seed=1, share=1.0, per_publication=None, random_ids=2, band=(10, 11))

    assert sorted((e["post_id"], tuple(e["frames"])) for e in worklist) == [
        (10, ("ranked", "random")),
        (11, ("ranked", "random")),
    ]
    assert json.loads((data.root / "sample.json").read_text())["band"] == [10, 11]


# --- scan -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "payload", "outcome"),
    [
        (200, {"type": "success", "fraction_ai": 0.7, "fraction_ai_assisted": 0.1, "fraction_human": 0.2}, "scored"),
        (200, {"type": "disabled"}, "disabled"),
        (200, {"type": "error", "header": "Subscription required"}, "paid"),
        (200, {"type": "error", "header": "Not enough text"}, "too_short"),
        (200, {"type": "error", "header": "Something new"}, "error"),
        (404, None, "dead"),
        (503, None, "error"),
    ],
)
def test_detection_responses_map_onto_outcomes(status, payload, outcome):
    assert scan.parse_detection(1, status, payload)["outcome"] == outcome


def test_scan_resumes_and_retries_only_unanswered_errors(data, monkeypatch):
    store.write(data.worklist_file, [{"post_id": i, "frames": ["ranked"]} for i in (1, 2)])
    answers = {1: FakeResponse(200, {"type": "disabled"}), 2: FakeResponse(503)}
    monkeypatch.setattr(http.requests, "get", lambda url, **k: answers[int(url.rsplit("-", 1)[1])])

    scan.run_scan(data)
    assert scan.pending(data) == []
    assert [e["post_id"] for e in scan.pending(data, retry_errors=True)] == [2]


# --- report -----------------------------------------------------------------


def _scan_row(post_id, frames, ai, publication):
    return {
        "post_id": post_id,
        "frames": frames,
        "outcome": "scored",
        "fraction_ai": ai,
        "fraction_ai_assisted": 0.0,
        "fraction_human": 1 - ai,
        "publication_id": publication,
        "publication_language": "en",
        "header": None,
        "http_status": 200,
    }


def test_publication_weighting_counts_each_publication_once(data):
    data.write_period(PERIOD)
    rows = [_scan_row(i, ["ranked"], 1.0, 1) for i in range(9)] + [_scan_row(9, ["ranked"], 0.0, 2)]
    store.write(data.scans_file, rows)
    store.write(data.worklist_file, [{"post_id": r["post_id"], "frames": ["ranked"]} for r in rows])
    posts = report.scored(report.load_posts(data, PERIOD), "ranked")

    assert report.estimate(posts, lambda p: p.ai, report.per_post)[0] == pytest.approx(90.0)
    assert report.estimate(posts, lambda p: p.ai, report.per_publication)[0] == pytest.approx(50.0)


def test_a_random_post_counts_only_once_dated_inside_the_period(data):
    data.write_period(PERIOD)
    store.write(data.scans_file, [_scan_row(1, ["random"], 1.0, 1), _scan_row(2, ["random"], 1.0, 2)])
    store.write(data.dates_file, [{"post_id": 1, "http_status": 200, "post_date": "2026-08-01T00:00:00Z"}])

    posts = report.load_posts(data, PERIOD)

    assert [p.post_id for p in report.scored(posts, "random")] == [1]
    assert "Substack posts" in report.render(data)


def test_a_census_rerun_reuses_the_saved_period_and_refuses_a_different_one(data):
    from substack_pangram.cli import _census_period

    with pytest.raises(SystemExit):
        _census_period(data, None, None)
    data.write_period(PERIOD)
    assert _census_period(data, None, None) == PERIOD
    with pytest.raises(SystemExit):
        _census_period(data, None, "2026-12-01T00:00:00+00:00")
