"""The reward and settled scan windows — their single source of truth.

``REWARD_START`` / ``REWARD_END`` are the hard, exclusive-output boundary:
history outside calendar year 2026 may be scanned to reconstruct opening state,
but it must never produce a reward amount or appear in a reward rollup.

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

# Reward amounts are deliberately limited to calendar year 2026. Keep this
# independent from DEFAULT_END: the deployed scan cutoff can move without
# silently expanding the authorized reward period.
REWARD_START = date(2026, 1, 1)
REWARD_END = date(2027, 1, 1)

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
