"""The settled scan window — THE one place the deployed cutoff lives.

``DEFAULT_END`` is EXCLUSIVE: events on/after it are out of the settled
window. ``LAST_SETTLED_DAY`` is the INCLUSIVE calendar day every daily series
must extend to — the no-transaction-day TWA fill, the share->asset conversion
series, the sp deployment idle series. They must all reach exactly this day:
a shorter series silently prices/fills the tail with fallbacks (the July-2026
END_CAP bug), a longer one invents days beyond the settlement.

Extending the settlement window to a new month = bump DEFAULT_END here and
re-run the chunked pipeline with a fresh chunks dir. Nothing else moves.
"""
from datetime import date, timedelta

# Deployed cutoff: events on/after 2026-09-01 are out of the settled window.
DEFAULT_END = date(2026, 9, 1)
LAST_SETTLED_DAY = DEFAULT_END - timedelta(days=1)
