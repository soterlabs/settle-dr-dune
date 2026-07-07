/**
 * Single source of truth for the DR settlement cutoff. The pipeline settles
 * COMPLETE months through SETTLE_MONTH (inclusive); later (partial) data is
 * excluded everywhere.
 *
 * The SQL files in queries/ carry the same cutoff as hardcoded date literals
 * (`least(timestamp '{{end_date}}', timestamp '<end-exclusive>')` on event
 * scans, `least(current_date, date '<last-day>')` on idle-fills/calendars).
 * To move the cutoff, run
 *
 *   npx tsx src/scripts/set-settlement-cutoff.ts 2026-06
 *
 * which rewrites both this constant and the SQL literals, then push + re-run
 * the Dune queries.
 */
export const SETTLE_MONTH = '2026-06';

/** Last day of the settlement month, e.g. '2026-05-31'. */
export function settleLastDay(month: string = SETTLE_MONTH): string {
  const [y, m] = month.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${y}-${String(m).padStart(2, '0')}-${String(last).padStart(2, '0')}`;
}

/** First day of the month AFTER the settlement month (exclusive scan cutoff). */
export function settleEndExclusive(month: string = SETTLE_MONTH): string {
  const [y, m] = month.split('-').map(Number);
  const ny = m === 12 ? y + 1 : y;
  const nm = m === 12 ? 1 : m + 1;
  return `${ny}-${String(nm).padStart(2, '0')}-01`;
}
