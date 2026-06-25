-- =============================================================================
-- DIAGNOSTIC — daily sUSDS balance for 0xbbbb...eeffcb when tagged ref 1007
-- =============================================================================
select
    dt,
    blockchain,
    symbol as token,
    ref_code,
    time_weighted_avg_balance as twa_shares,
    day_type
from query_7640317
where user_addr = 0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb
  and ref_code  = 1007
order by dt
