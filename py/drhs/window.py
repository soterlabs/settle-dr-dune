"""The settled scan window — THE one place the deployed cutoff lives.

``DEFAULT_END`` is EXCLUSIVE: events on/after it are out of the settled
window. ``LAST_SETTLED_DAY`` is the INCLUSIVE calendar day every daily series
must extend to — the no-transaction-day TWA fill, the share->asset conversion
series, the sp deployment idle series. They must all reach exactly this day:
a shorter series silently prices/fills the tail with fallbacks (the July-2026
END_CAP bug), a longer one invents days beyond the settlement.

Extending the settlement window to a new month = bump DEFAULT_END here,
re-run the chunked pipeline with a fresh chunks dir, and regenerate the
workbook (its month range derives from here). The Skybase reconciliation is
deliberately frozen at its paid scope and does NOT track this window.
"""
from datetime import date, datetime, timedelta, timezone

# Deployed cutoff: events on/after 2026-09-01 are out of the settled window.
DEFAULT_END = date(2026, 9, 1)
LAST_SETTLED_DAY = DEFAULT_END - timedelta(days=1)


def midnight_ts(d: date) -> int:
    """UTC-midnight epoch of calendar day ``d`` — the day-boundary conversion
    (a copy that drops the tzinfo shifts every boundary by the host's UTC
    offset, invisibly on UTC prod)."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def beyond_cutoff_message(end: date) -> str:
    """The shared operator message for an --end beyond the deployed cutoff —
    one string with one home, so the callers cannot drift apart again."""
    return (f"--end {end} is beyond the deployed scan cutoff {DEFAULT_END}: the "
            "fill and conversion caps derive from it, so later months would be "
            "silently empty. Extend the settlement window first (bump "
            "DEFAULT_END in drhs/window.py — the single home).")
