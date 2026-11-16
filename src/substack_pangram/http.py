"""One GET helper for every Substack endpoint the study calls.

All endpoints are public and unauthenticated. The helper retries only what is
not yet an answer -- a 429 (too fast), a 5xx (gateway failure) or no response at
all -- each on its own budget, and returns everything else on the first try.
"""

import logging
import time
from typing import Any

import requests

from substack_pangram.config import TIMEOUT_SECONDS, USER_AGENT

log = logging.getLogger(__name__)

TRANSIENT_ATTEMPTS = 3
TRANSIENT_BACKOFF_SECONDS = 5.0


def get(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    delay: float,
    rate_limit_attempts: int = 4,
    rate_limit_backoff: float = 20.0,
) -> requests.Response | None:
    """GET ``url``, then sleep ``delay`` seconds.

    A 429 waits ``rate_limit_backoff * attempt`` seconds; a 5xx or a failed
    request waits ``TRANSIENT_BACKOFF_SECONDS * attempt``.

    Returns:
        The last response, including a 429 or 5xx that outlasted its retries,
        so the caller can record the status. ``None`` if no attempt produced a
        response.
    """
    throttled = transient = 0
    response: requests.Response | None = None
    while True:
        try:
            response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as e:
            transient += 1
            log.debug("Request failed for %s (%d): %s", url, transient, e)
            if transient >= TRANSIENT_ATTEMPTS:
                time.sleep(delay)
                return None
            time.sleep(TRANSIENT_BACKOFF_SECONDS * transient)
            continue

        if response.status_code == 429:
            throttled += 1
            if throttled >= rate_limit_attempts:
                break
            log.info("Rate limited on %s (attempt %d/%d)", url, throttled, rate_limit_attempts)
            time.sleep(rate_limit_backoff * throttled)
            continue

        if response.status_code >= 500:
            transient += 1
            if transient >= TRANSIENT_ATTEMPTS:
                break
            time.sleep(TRANSIENT_BACKOFF_SECONDS * transient)
            continue

        break

    time.sleep(delay)
    return response


def is_retryable(http_status: int) -> bool:
    """Whether a recorded status was never an answer, so asking again could settle it."""
    return http_status == 0 or http_status == 429 or http_status >= 500
