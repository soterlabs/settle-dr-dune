import BigNumber from 'bignumber.js';
import { groupBy } from 'lodash';

import {
  Event as EventTypeOnly,
  UserState,
  ReferralTVL,
  MonthlyRewards,
  ProcessedMonth,
  PreviousMonthState,
  TokenPrice,
  Farm,
  MonthlyReward,
  BlockTimestampRecord,
  UserHistories
} from '@/update-distribution-rewards-calculations/calculations/models/models.ts';
import { config } from '@/update-distribution-rewards-calculations/calculations/config.ts';
import {
  fetchAndMergeTokenPrices,
  getDepositBasedTokenRatio,
  mergeAndCreateRatio
} from '@/update-distribution-rewards-calculations/calculations/fetchTokenPrices.ts';
import { Kysely, sql } from 'kysely';
import {
  getAdditionalPercentagesForDate,
  getGenesisBlockTimestamp,
  getLatestSnapshotDate,
  getMonthBlocks,
  getOrCreateNetwork,
  getProcessedMonthRewardsBeforeDate,
  getUserMonthlySnapshotRows
} from '@/update-distribution-rewards-calculations/queries.ts';
import {SupabaseDB} from "../../db/schema.ts";
import database from "../../db/db.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SP_FARM_ALLOCATION = 0.9;
const DEFAULT_ALLOCATION = 1;
const SECONDS_IN_YEAR = new BigNumber(31536000);
const ZERO_ADDRESS = '0x0000000000000000000000000000000000000000';
const BN_ZERO = new BigNumber(0);
const BASE_RATE = 0.002;
const UNIQUE_ADDITIONAL_PERCENTAGE_2026 = 0.003;

const SPARK_RATE = apyToAnnualizedDailyRate(
  config.accessibilityRewards.rewardRates.sparkAPY + BASE_RATE
);
const NON_SPARK_REF_RATE = apyToAnnualizedDailyRate(
  config.accessibilityRewards.rewardRates.nonSparkRewardPercentage + BASE_RATE
);
const NEW_DEFAULT_RATE = apyToAnnualizedDailyRate(
  UNIQUE_ADDITIONAL_PERCENTAGE_2026 + BASE_RATE
);

function apyToAnnualizedDailyRate(apy: number): number {
  const growthFactor = 1.0 + apy;
  const dailyRate = Math.exp(Math.log(growthFactor) / 365.0) - 1.0;
  return 365.0 * dailyRate;
}

// ---------------------------------------------------------------------------
// Internal BigNumber-based types used during calculation
//
// These mirror the external model types but use BigNumber for precision.
// Conversion to/from the external `number`-based types happens only at
// the boundaries (DB read / final output).
// ---------------------------------------------------------------------------

/** Internal TVL state per user – keeps tvl as BigNumber throughout calc. */
interface BNUserState {
  tvl: BigNumber;
  referral: string;
  hasBeenTagged: boolean;
}

/** userId → BNUserState */
type BNUserStates = Record<string, BNUserState>;

/** referralCode → BigNumber TVL */
type BNReferralTVL = Record<string, BigNumber>;

/** referralCode → BigNumber accumulated reward */
type BNReferralRewards = Record<string, BigNumber>;

// ---------------------------------------------------------------------------
// Conversion helpers: external (number) ↔ internal (BigNumber)
// ---------------------------------------------------------------------------

function userStatesToBN(states: UserHistories): BNUserStates {
  const out: BNUserStates = {};
  for (const key in states) {
    const s = states[key];
    out[key] = {
      tvl: new BigNumber(s.tvl),
      referral: s.referral,
      hasBeenTagged: s.hasBeenTagged
    };
  }
  return out;
}

function bnUserStatesToExternal(states: BNUserStates): UserHistories {
  const out: UserHistories = {};
  for (const key in states) {
    const s = states[key];
    out[key] = {
      tvl: s.tvl.toNumber(),
      referral: s.referral,
      hasBeenTagged: s.hasBeenTagged
    };
  }
  return out;
}
function bnReferralTVLToExternal(tvl: BNReferralTVL): ReferralTVL {
  const out: ReferralTVL = {};
  for (const key in tvl) {
    out[key] = tvl[key].toNumber();
  }
  return out;
}

// ---------------------------------------------------------------------------
// Shared accrual helper (eliminates copy-paste across event processors)
// ---------------------------------------------------------------------------

/**
 * Accrue time-weighted rewards for every referral bucket.
 * Called once per event, before the event mutates TVL.
 */
function accrueRewards(
  referralTVL: BNReferralTVL,
  referralRewards: BNReferralRewards,
  timeDelta: BigNumber
): void {
  if (timeDelta.lte(0)) return;
  for (const ref in referralTVL) {
    const tvl = referralTVL[ref] ?? BN_ZERO;
    referralRewards[ref] = (referralRewards[ref] ?? BN_ZERO).plus(
      tvl.times(timeDelta).div(SECONDS_IN_YEAR)
    );
  }
}

/** Clamp a BigNumber to zero (never go negative). */
function clampZero(value: BigNumber): BigNumber {
  return BigNumber.max(BN_ZERO, value);
}

// ---------------------------------------------------------------------------
// Aggregated event row types & queries
// ---------------------------------------------------------------------------

interface AggregatedEventRow {
  transactionHash: string;
  event: string;
  returnValues: Record<string, string>;
  amount: string | null;
  blockNumber: number;
  logIndex: number;
  contractAddress: string;
  referral: string | null;
  blockTimestamp: number;
  isoDate: string;
  transactionIndex: number;
}

interface AggregatedEventRowDb {
  transaction_hash: string;
  event: string;
  return_values: Record<string, string>;
  amount: string | null;
  block_number: number;
  log_index: number;
  contract_address: string;
  referral: string | null;
  block_timestamp: number;
  iso_date: string;
  transaction_index: number;
}

export async function getAggregatedEventRows(
    db: Kysely<SupabaseDB>,
    minMonthBlock: number,
    maxMonthBlock: number,
    contractAddress: string,
    valueDenomination: number
): Promise<AggregatedEventRow[]> {
  const result = await sql<{
    transaction_hash: string;
    event: string;
    return_values: Record<string, string>;
    amount: string | null;
    block_number: number;
    log_index: number;
    contract_address: string;
    referral: string | null;
    block_timestamp: number;
    iso_date: string;
    transaction_index: number;
  }>`
    WITH referral_per_tx AS (
      SELECT DISTINCT ON (e.transaction_hash)
        e.transaction_hash,
        e.return_values ->> 'referral' AS referral
      FROM events_accessibility_rewards e
      WHERE e.block_number >= ${minMonthBlock}
        AND e.block_number <= ${maxMonthBlock}
        AND e.contract_address = ${contractAddress}
        AND e.event = 'Referral'
      ORDER BY e.transaction_hash, e.log_index
    )
    SELECT e.transaction_hash,
           e.event,
           e.return_values,
           CASE
             WHEN e.return_values ->> 'amount' IS NOT NULL THEN
               ROUND((e.return_values ->> 'amount')::numeric / ${valueDenomination}, 6)::text
             WHEN e.return_values ->> 'shares' IS NOT NULL THEN
               ROUND((e.return_values ->> 'shares')::numeric / ${valueDenomination}, 6)::text
             WHEN e.return_values ->> 'assets' IS NOT NULL THEN
               ROUND((e.return_values ->> 'assets')::numeric / ${valueDenomination}, 6)::text
             WHEN e.return_values ->> 'value' IS NOT NULL THEN
               ROUND((e.return_values ->> 'value')::numeric / ${valueDenomination}, 6)::text
             END                                                                AS amount,
           e.block_number,
           e.log_index,
           e.contract_address,
           COALESCE(rpt.referral, 'untagged')                                  AS referral,
           bts.timestamp                                                       AS block_timestamp,
           to_char(to_timestamp(bts.timestamp), 'YYYY-MM-DD"T"HH24:MI:SS.MSZ') AS iso_date,
           e.transaction_index
    FROM events_accessibility_rewards e
           LEFT JOIN referral_per_tx rpt ON rpt.transaction_hash = e.transaction_hash
           LEFT JOIN block_timestamp_accessibility_rewards bts
                     ON bts.block_number = e.block_number
                       AND bts.network = e.network
    WHERE e.block_number >= ${minMonthBlock}
      AND e.block_number <= ${maxMonthBlock}
      AND e.contract_address = ${contractAddress}
      AND e.event IN ('Staked', 'Deposit', 'Withdraw', 'Withdrawn', 'Transfer')
    ORDER BY e.block_number, e.transaction_index, e.log_index;
  `.execute(db);

  return result.rows.map((row: AggregatedEventRowDb) => ({
    transactionHash: row.transaction_hash,
    event: row.event,
    returnValues: row.return_values,
    amount: row.amount,
    blockNumber: row.block_number,
    logIndex: row.log_index,
    contractAddress: row.contract_address,
    referral: row.referral,
    blockTimestamp: row.block_timestamp,
    isoDate: row.iso_date,
    transactionIndex: row.transaction_index
  }));
}

export async function getAggregatedEventRowsChunked(
  db: Kysely<SupabaseDB>,
  minMonthBlock: number,
  maxMonthBlock: number,
  contractAddress: string,
  valueDenomination: number,
  chunks: number = 3,
  maxParallel: number = 1
): Promise<AggregatedEventRow[]> {
  const blockRange = maxMonthBlock - minMonthBlock;
  const chunkSize = Math.ceil(blockRange / chunks);

  const allChunks: Array<[number, number]> = [];
  for (let i = 0; i < chunks; i++) {
    const chunkMin = minMonthBlock + i * chunkSize;
    const chunkMax = Math.min(chunkMin + chunkSize - 1, maxMonthBlock);
    allChunks.push([chunkMin, chunkMax]);
  }

  let results: AggregatedEventRow[] = [];

  for (let i = 0; i < allChunks.length; i += maxParallel) {
    const batch = allChunks.slice(i, i + maxParallel);
    console.log(`Processing non-swap batch ${Math.floor(i / maxParallel) + 1}`);

    const batchResults = await Promise.all(
      batch.map(([min, max]) =>
        getAggregatedEventRows(db, min, max, contractAddress, valueDenomination)
      )
    );

    for (const batchResult of batchResults) {
      results = results.concat(batchResult);
    }
  }

  return results;
}

async function getSwapAggregatedEventRows(
    db: Kysely<SupabaseDB>,
    minMonthBlock: number,
    maxMonthBlock: number,
    contractAddress: string,
    tokenAddress: string,
    valueDenomination: number
): Promise<AggregatedEventRow[]> {
  const result = await sql<{
    transaction_hash: string;
    event: string;
    return_values: Record<string, string>;
    amount: string | null;
    block_number: number;
    log_index: number;
    contract_address: string;
    referral: string | null;
    block_timestamp: number;
    iso_date: string;
    transaction_index: number;
  }>`
    SELECT e.transaction_hash,
           e.event,
           e.return_values,
           CASE
             WHEN e.event = 'Swap' THEN
               ROUND(
                 (
                   CASE
                     WHEN e.return_values ->> 'assetIn' = ${tokenAddress}
                       THEN (e.return_values ->> 'amountIn')::numeric
                     ELSE (e.return_values ->> 'amountOut')::numeric
                     END
                   ) / ${valueDenomination}, 6
               )::text
             END                                                                AS amount,
           e.block_number,
           e.log_index,
           e.contract_address,
           COALESCE(e.return_values ->> 'referralCode', 'untagged')            AS referral,
           bts.timestamp                                                       AS block_timestamp,
           to_char(to_timestamp(bts.timestamp), 'YYYY-MM-DD"T"HH24:MI:SS.MSZ') AS iso_date,
           e.transaction_index
    FROM events_accessibility_rewards e
           LEFT JOIN block_timestamp_accessibility_rewards bts
                     ON bts.block_number = e.block_number AND bts.network = e.network
    WHERE e.block_number >= ${minMonthBlock}
      AND e.block_number <= ${maxMonthBlock}
      AND e.contract_address = ${contractAddress}
      AND (
      (e.event = 'Swap' AND (
        e.return_values ->> 'assetIn' = ${tokenAddress} OR
        e.return_values ->> 'assetOut' = ${tokenAddress}
        ))
        OR e.event = 'Transfer'
      )
    ORDER BY e.block_number, e.transaction_index, e.log_index;
  `.execute(db);

  return result.rows.map((row: AggregatedEventRowDb) => ({
    transactionHash: row.transaction_hash,
    event: row.event,
    returnValues: row.return_values,
    amount: row.amount,
    blockNumber: row.block_number,
    logIndex: row.log_index,
    contractAddress: row.contract_address,
    referral: row.referral,
    blockTimestamp: row.block_timestamp,
    isoDate: row.iso_date,
    transactionIndex: row.transaction_index
  }));
}

// ---------------------------------------------------------------------------
// Snapshot restore
// ---------------------------------------------------------------------------

async function restoreMonthlySnapshotAndMonthStart(
  db: Kysely<SupabaseDB>,
  farmName: string,
  networkId: number,
  chainNetwork: string
): Promise<{
  userHistories: UserHistories;
  currentMonthStart: Date;
  didRestoreFromDb: boolean;
}> {
  const latestRow = await getLatestSnapshotDate(db, farmName, networkId);

  let userHistories: UserHistories = {};
  let currentMonthStart: Date;
  let didRestoreFromDb = false;

  if (latestRow?.date) {
    const snapshotRows = await getUserMonthlySnapshotRows(
      db,
      farmName,
      networkId,
      latestRow.date
    );

    for (const row of snapshotRows) {
      userHistories[row.user_id] = {
        tvl: Number(row.tvl),
        referral: row.referral_code,
        hasBeenTagged: row.referral_code !== 'untagged'
      };
    }

    const [year, month, day] = latestRow.date.split('-').map(Number);
    const dateObj = new Date(Date.UTC(year, month - 1, day));
    currentMonthStart = new Date(
      Date.UTC(dateObj.getUTCFullYear(), dateObj.getUTCMonth() + 1, 1)
    );
    didRestoreFromDb = true;

    console.log(
      `Restored ${snapshotRows.length} userHistories from DB snapshot for ${latestRow.date}.`,
      'Starting calculation from',
      currentMonthStart.toISOString()
    );
  } else {
    const firstBlock = await getGenesisBlockTimestamp(db, chainNetwork);
    if (!firstBlock) throw new Error('No genesis block found for this network');

    userHistories = {};
    currentMonthStart = new Date(firstBlock.timestamp * 1000);

    console.log(
      'No userHistories snapshot found. Starting from chain genesis:',
      currentMonthStart.toISOString()
    );
  }

  return { userHistories, currentMonthStart, didRestoreFromDb };
}

// ---------------------------------------------------------------------------
// Price helpers
// ---------------------------------------------------------------------------

async function getProcessedPrices(
  farm: { tokenCode: string },
  db: Kysely<SupabaseDB>
): Promise<TokenPrice[]> {
  switch (farm.tokenCode) {
    case 'USDS':
      return [];
    case 'stUSDS':
      return await getDepositBasedTokenRatio(db, farm.tokenCode);
    case 'spUSDC':
    case 'spUSDT':
    case 'spPYUSD':
      const proxyTokens = await getDepositBasedTokenRatio(db, farm.tokenCode);
      const baseTokenUsdsRatio = await fetchAndMergeTokenPrices(
        'USDS',
        farm.tokenCode.replace('sp', '')
      );
      return mergeAndCreateRatio(baseTokenUsdsRatio, proxyTokens);
    default:
      return await fetchAndMergeTokenPrices('USDS', 'sUSDS');
  }
}

// ---------------------------------------------------------------------------
// Main orchestrator
// ---------------------------------------------------------------------------

export async function processMonthlyRewards(
  farmName: string,
  calculateMissingData: boolean = false,
  calculateCurrentMonth: boolean = false
): Promise<{ processedMonths: ProcessedMonth[] }> {
  const db = database.pools.readOnly as Kysely<SupabaseDB>;

  const farmNetworkSplit = farmName.split(' - ');
  const farm = config.accessibilityRewards.partners.find(
    (partner) =>
      partner.partnerName.toLowerCase() === farmNetworkSplit[0].toLowerCase() &&
      partner.network.toLowerCase() === farmNetworkSplit[1].toLowerCase()
  );
  const valueDenomination = farm?.decimals ? Math.pow(10, farm.decimals) : 1e18;

  if (!farm) {
    throw new Error(`Farm ${farmName} not found!`);
  }
  const processedPrices = await getProcessedPrices(farm, db);

  const allResults: ProcessedMonth[] = [];
  let startTVLSnapshots: MonthlyReward[] = [];

  const networkId = await getOrCreateNetwork(db, farmNetworkSplit[1]);
  let { userHistories, currentMonthStart } =
    await restoreMonthlySnapshotAndMonthStart(
      db,
      farm.partnerName,
      networkId,
      farm.network
    );

  const previousRewards = await getProcessedMonthRewardsBeforeDate(
    db,
    farm.partnerName,
    networkId,
    currentMonthStart.toISOString().slice(0, 10)
  );

  const grouped = groupBy(previousRewards, (r) => r.date.slice(0, 7));
  for (const [month, rewardsArr] of Object.entries(grouped)) {
    const rewards: MonthlyRewards = {};
    for (const r of rewardsArr) {
      rewards[r.referral_code] = {
        referralCode: r.referral_code,
        monthEndEligibleTVL: r.eligible_tvl,
        rewards: r.incentive_amount,
        rewardToPay: r.incentive_amount_to_pay
      };
    }
    allResults.push({ month, rewards, userHistories: {} });
  }

  const currentDate = new Date();
  console.log('Run parameters:', {
    now: currentDate.toISOString(),
    startMonth: currentMonthStart.toISOString()
  });

  while (currentMonthStart < currentDate && calculateMissingData) {
    if (
      isCurrentMonth(currentMonthStart, currentDate) &&
      !calculateCurrentMonth
    )
      break;
    const { monthStartTimestamp, monthEndTimestamp } =
      getMonthBoundaries(currentMonthStart);

    const monthBlocks: BlockTimestampRecord[] = await getMonthBlocks(
      db,
      monthStartTimestamp,
      monthEndTimestamp,
      farm.network
    );

    if (monthBlocks.length > 0) {
      const minBlock = monthBlocks[0].blockNumber;
      const maxBlock = monthBlocks[monthBlocks.length - 1].blockNumber;
      let monthEvents: AggregatedEventRow[];
      if (farm.tokenAddress) {
        monthEvents = await getSwapAggregatedEventRowsChunked(
          db,
          minBlock,
          maxBlock,
          farm.contractAddress,
          farm.tokenAddress,
          valueDenomination,
          4
        );
      } else {
        monthEvents = await getAggregatedEventRowsChunked(
          db,
          minBlock,
          maxBlock,
          farm.contractAddress,
          valueDenomination,
          4
        );
      }

      const flattenedEvents: EventTypeOnly[] = processEventsAgg(
        monthEvents as AggregatedEventRow[],
        valueDenomination
      );

      const previousMonthState: PreviousMonthState = {
        userStates: userHistories,
        referralTVL: snapshotsToTVLMap(startTVLSnapshots)
      };
      const rewardStats = calculateMonthlyRewards(
        flattenedEvents,
        monthStartTimestamp,
        monthEndTimestamp,
        farm.tokenAddress,
        previousMonthState,
        processedPrices,
        !!farm.l2 || farm.partnerName.toLowerCase().startsWith('spk')
      );
      userHistories = filterUserHistories(rewardStats.endingState.userStates);
      startTVLSnapshots = tvlMapToSnapshots(rewardStats.rewards);

      const additionalPercentages = await getTransformedAdditionalPercentages(
        db,
        currentMonthStart.toISOString()
      );

      const monthWithPayments = addPaymentCalculations(
        currentMonthStart.toISOString().slice(0, 7),
        rewardStats.rewards,
        farm,
        additionalPercentages
      );
      const newUserHistories = Object.fromEntries(
        Object.entries(userHistories).map(([key, value]) => [key, { ...value }])
      );
      const processingCurrentMonth = currentDate
        .toISOString()
        .startsWith(monthWithPayments.month);
      allResults.push({
        ...monthWithPayments,
        userHistories: processingCurrentMonth ? {} : { ...newUserHistories }
      });
    }
    currentMonthStart = new Date(
      Date.UTC(
        currentMonthStart.getUTCFullYear(),
        currentMonthStart.getUTCMonth() + 1,
        1
      )
    );
  }
  return { processedMonths: allResults };
}

// ---------------------------------------------------------------------------
// Event flattening (raw DB rows → typed events)
// ---------------------------------------------------------------------------

export function processEventsAgg(
  rawEvents: AggregatedEventRow[],
  valueDenomination: number
): EventTypeOnly[] {
  const allEvents: EventTypeOnly[] = [];
  const seen = new Set<string>();

  for (const evt of rawEvents) {
    if (!evt) continue;

    const uniqueKey = `${evt.transactionHash}-${evt.logIndex}-${evt.contractAddress}`;
    if (seen.has(uniqueKey)) continue;
    seen.add(uniqueKey);

    const rv = evt.returnValues || {};
    const address = rv.owner || rv.user || rv.receiver || rv.to || null;

    const value = rv.value;
    let parsedAmount: number | null = null;

    if (evt.amount !== null && evt.amount !== undefined) {
      parsedAmount = parseFloat(evt.amount);
    }
    if ((parsedAmount === null || isNaN(parsedAmount)) && value) {
      parsedAmount = parseFloat(value as string) / valueDenomination;
    }
    if (parsedAmount === null || isNaN(parsedAmount)) {
      parsedAmount = 0;
    }

    allEvents.push({
      event: evt.event,
      amount: parsedAmount,
      address: address ? String(address).toLowerCase() : null,
      referral: evt.referral || 'untagged',
      blockTimestamp: evt.blockTimestamp,
      blockNumber: evt.blockNumber,
      transactionHash: evt.transactionHash,
      transactionIndex: evt.transactionIndex,
      returnValues: evt.returnValues || {},
      contractAddress: evt.contractAddress,
      logIndex: evt.logIndex
    });
  }
  return allEvents;
}

// ---------------------------------------------------------------------------
// Chunked swap event fetching
// ---------------------------------------------------------------------------

export async function getSwapAggregatedEventRowsChunked(
  db: Kysely<SupabaseDB>,
  minMonthBlock: number,
  maxMonthBlock: number,
  contractAddress: string,
  tokenAddress: string,
  valueDenomination: number,
  chunks: number = 4,
  maxParallel: number = 1
): Promise<AggregatedEventRow[]> {
  const blockRange = maxMonthBlock - minMonthBlock;
  const chunkSize = Math.ceil(blockRange / chunks);

  const allChunks: Array<[number, number]> = [];
  for (let i = 0; i < chunks; i++) {
    const chunkMin = minMonthBlock + i * chunkSize;
    const chunkMax = Math.min(chunkMin + chunkSize - 1, maxMonthBlock);
    allChunks.push([chunkMin, chunkMax]);
  }

  let results: AggregatedEventRow[] = [];

  for (let i = 0; i < allChunks.length; i += maxParallel) {
    const batch = allChunks.slice(i, i + maxParallel);
    console.log(`Processing batch ${Math.floor(i / maxParallel) + 1}`);

    const batchResults = await Promise.all(
      batch.map(([min, max]) =>
        getSwapAggregatedEventRows(
          db,
          min,
          max,
          contractAddress,
          tokenAddress,
          valueDenomination
        )
      )
    );

    for (const batchResult of batchResults) {
      results = results.concat(batchResult);
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Core monthly reward calculation
// ---------------------------------------------------------------------------

export function calculateMonthlyRewards(
  events: EventTypeOnly[],
  monthStartTimestamp: number,
  monthEndTimestamp: number,
  tokenAddress: string | undefined,
  previousMonthState: PreviousMonthState,
  processedPrices: TokenPrice[],
  isSUSDC: boolean
): {
  rewards: MonthlyRewards;
  endingState: {
    userStates: Record<string, UserState>;
    referralTVL: ReferralTVL;
    timestamp: number;
  };
} {
  // Convert external number-based state → internal BigNumber state
  const userStates: BNUserStates = userStatesToBN(
    previousMonthState.userStates
  );
  const referralTVL: BNReferralTVL = rebuildReferralTVL(userStates);
  const referralRewards: BNReferralRewards = {};
  for (const ref in referralTVL) referralRewards[ref] = BN_ZERO;

  const sortedEvents = sortEventsChronologically(events);
  let currentTimestamp = monthStartTimestamp;
  let prevHash: string | undefined, prevEvent: string | undefined;

  for (const evt of sortedEvents) {
    let skipTransaction = false;
    if (
      evt.event === 'Swap' &&
      prevEvent === 'Transfer' &&
      prevHash?.toLowerCase() === evt.transactionHash?.toLowerCase()
    )
      skipTransaction = true;
    else {
      prevEvent = evt.event;
      prevHash = evt.transactionHash;
    }
    const contractAddress = evt.contractAddress?.toLowerCase();
    const userKey = getUserKey(evt, contractAddress);
    if (!contractAddress || evt.amount == null) continue;

    const timeDelta = getWeightedAveragePricePrecise(
      new BigNumber(Math.min(evt.blockTimestamp, monthEndTimestamp)),
      new BigNumber(currentTimestamp),
      processedPrices
    );

    if (userKey && !userStates[userKey])
      userStates[userKey] = {
        tvl: BN_ZERO,
        referral: 'untagged',
        hasBeenTagged: false
      };

    const processed = processEvent(
      evt,
      userStates,
      referralTVL,
      referralRewards,
      timeDelta,
      skipTransaction,
      tokenAddress
    );
    if (processed) {
      currentTimestamp = Math.min(evt.blockTimestamp, monthEndTimestamp);
    }
    if (currentTimestamp >= monthEndTimestamp) break;
  }

  // Final accrual for time between last event and month end
  const finalTimeDelta = getWeightedAveragePricePrecise(
    new BigNumber(monthEndTimestamp),
    new BigNumber(currentTimestamp),
    processedPrices
  );
  accrueRewards(referralTVL, referralRewards, finalTimeDelta);

  // Build results, converting back to external number types
  const results: MonthlyRewards = {};
  for (const referral in referralRewards) {
    const reward = referralRewards[referral];
    const tvl = referralTVL[referral] ?? BN_ZERO;

    if (reward.gt(0) || tvl.gt(0)) {
      const untaggedReferral =
        referral === 'untagged' && isSUSDC ? '127' : referral;
      results[untaggedReferral] = {
        referralCode: untaggedReferral,
        monthEndEligibleTVL: tvl.toNumber(),
        rewards: reward.toNumber(),
        rewardToPay: 0
      };
    }
  }

  return {
    rewards: results,
    endingState: {
      userStates: bnUserStatesToExternal(userStates),
      referralTVL: bnReferralTVLToExternal(referralTVL),
      timestamp: monthEndTimestamp
    }
  };
}

// ---------------------------------------------------------------------------
// Event processors (BigNumber throughout, shared accrual helper)
// ---------------------------------------------------------------------------

export function processEvent(
  evt: EventTypeOnly,
  userStates: BNUserStates,
  referralTVL: BNReferralTVL,
  referralRewards: BNReferralRewards,
  timeDelta: BigNumber,
  skipTransaction: boolean,
  tokenAddress?: string
): boolean {
  const contractAddress = evt.contractAddress?.toLowerCase();
  if (isDepositEvent(evt, tokenAddress)) {
    return processDepositEvent(
      evt,
      userStates,
      referralTVL,
      referralRewards,
      timeDelta,
      skipTransaction,
      contractAddress
    );
  } else if (isWithdrawalEvent(evt, tokenAddress)) {
    return processWithdrawalEvent(
      evt,
      userStates,
      referralTVL,
      referralRewards,
      timeDelta,
      skipTransaction,
      contractAddress
    );
  } else if (evt.event === 'Transfer') {
    return processTransferEvent(
      evt,
      userStates,
      referralTVL,
      referralRewards,
      timeDelta,
      contractAddress
    );
  }
  return true;
}

export function processDepositEvent(
  evt: EventTypeOnly,
  userStates: BNUserStates,
  referralTVL: BNReferralTVL,
  referralRewards: BNReferralRewards,
  timeDelta: BigNumber,
  skipTransaction: boolean,
  contractAddress: string
): boolean {
  accrueRewards(referralTVL, referralRewards, timeDelta);

  let userKey = getUserKey(evt, contractAddress);
  if (evt.event === 'Transfer') {
    userKey = `${evt.returnValues?.to?.toLowerCase()}_${contractAddress}`;
    if (!userStates[userKey])
      userStates[userKey] = {
        tvl: BN_ZERO,
        referral: 'untagged',
        hasBeenTagged: false
      };
  }

  const userState = userStates[userKey];
  const oldTVL = userState.tvl;
  const previousReferral = userState.referral;
  const depositAmount = new BigNumber(evt.amount || 0);

  if (!skipTransaction) {
    userState.tvl = clampZero(userState.tvl.plus(depositAmount));
  }

  if (evt.referral && evt.referral !== 'untagged') {
    if (!userState.hasBeenTagged) {
      userState.hasBeenTagged = true;
      userState.referral = evt.referral;
      referralTVL['untagged'] = clampZero(
        (referralTVL['untagged'] ?? BN_ZERO).minus(oldTVL)
      );
      referralTVL[evt.referral] = (referralTVL[evt.referral] ?? BN_ZERO).plus(
        oldTVL
      );
    } else {
      userState.referral = evt.referral;
      referralTVL[previousReferral] = clampZero(
        (referralTVL[previousReferral] ?? BN_ZERO).minus(oldTVL)
      );
      referralTVL[evt.referral] = (referralTVL[evt.referral] ?? BN_ZERO).plus(
        oldTVL
      );
    }
  }

  if (!skipTransaction) {
    referralTVL[userState.referral] = (
      referralTVL[userState.referral] ?? BN_ZERO
    ).plus(depositAmount);
  }
  return true;
}

export function processWithdrawalEvent(
  evt: EventTypeOnly,
  userStates: BNUserStates,
  referralTVL: BNReferralTVL,
  referralRewards: BNReferralRewards,
  timeDelta: BigNumber,
  skipTransaction: boolean,
  contractAddress: string
): boolean {
  accrueRewards(referralTVL, referralRewards, timeDelta);

  let userKey = getUserKey(evt, contractAddress);
  if (evt.event === 'Transfer') {
    userKey = `${evt.returnValues?.from?.toLowerCase()}_${contractAddress}`;
    if (!userStates[userKey])
      userStates[userKey] = {
        tvl: BN_ZERO,
        referral: 'untagged',
        hasBeenTagged: false
      };
  }

  const userState = userStates[userKey];
  const evtAmount = new BigNumber(evt.amount || 0);
  const withdrawAmount = !skipTransaction
    ? BigNumber.min(userState.tvl, evtAmount)
    : BN_ZERO;

  userState.tvl = clampZero(userState.tvl.minus(withdrawAmount));
  referralTVL[userState.referral] = clampZero(
    (referralTVL[userState.referral] ?? BN_ZERO).minus(withdrawAmount)
  );
  return true;
}

export function processTransferEvent(
  evt: EventTypeOnly,
  userStates: BNUserStates,
  referralTVL: BNReferralTVL,
  referralRewards: BNReferralRewards,
  timeDelta: BigNumber,
  contractAddress: string
): boolean {
  const from = evt.returnValues?.from?.toLowerCase();
  const to = evt.returnValues?.to?.toLowerCase();
  const rawAmount = evt.amount ?? parseFloat(evt.returnValues?.value as string);
  if (
    !from ||
    !to ||
    !rawAmount ||
    from === ZERO_ADDRESS ||
    to === ZERO_ADDRESS
  )
    return false;

  accrueRewards(referralTVL, referralRewards, timeDelta);

  const amount = new BigNumber(rawAmount);
  const fromKey = `${from}_${contractAddress}`;
  if (!userStates[fromKey])
    userStates[fromKey] = {
      tvl: BN_ZERO,
      referral: 'untagged',
      hasBeenTagged: false
    };

  const senderState = userStates[fromKey];
  const transferAmount = BigNumber.min(senderState.tvl, amount);

  if (transferAmount.gt(0)) {
    senderState.tvl = clampZero(senderState.tvl.minus(transferAmount));
    referralTVL[senderState.referral] = clampZero(
      (referralTVL[senderState.referral] ?? BN_ZERO).minus(transferAmount)
    );

    const toKey = `${to}_${contractAddress}`;
    if (!userStates[toKey])
      userStates[toKey] = {
        tvl: BN_ZERO,
        referral: 'untagged',
        hasBeenTagged: false
      };

    const receiverState = userStates[toKey];
    const oldTVL = receiverState.tvl;

    receiverState.tvl = receiverState.tvl.plus(transferAmount);
    referralTVL[receiverState.referral] = (
      referralTVL[receiverState.referral] ?? BN_ZERO
    ).plus(transferAmount);
  }
  return true;
}

// ---------------------------------------------------------------------------
// Price / time helpers
// ---------------------------------------------------------------------------

export function getWeightedAveragePricePrecise(
  endTime: BigNumber,
  startTime: BigNumber,
  preprocessedPrices: TokenPrice[]
): BigNumber {
  if (!preprocessedPrices.length) {
    return endTime.minus(startTime);
  }
  let left = 0,
    right = preprocessedPrices.length - 1,
    startIdx = -1;
  while (left <= right) {
    const mid = (left + right) >> 1;
    if (new BigNumber(preprocessedPrices[mid].timestamp).lte(startTime)) {
      startIdx = mid;
      left = mid + 1;
    } else right = mid - 1;
  }
  let lastKnownRate =
    startIdx >= 0
      ? new BigNumber(preprocessedPrices[startIdx].rate)
      : new BigNumber(1);
  let totalWeightedValue = BN_ZERO;
  let currentTime = new BigNumber(startTime);
  for (
    let i = startIdx + 1;
    i < preprocessedPrices.length &&
    new BigNumber(preprocessedPrices[i].timestamp).lt(endTime);
    i++
  ) {
    const priceTime = new BigNumber(preprocessedPrices[i].timestamp);
    const duration = priceTime.minus(currentTime);
    totalWeightedValue = totalWeightedValue.plus(lastKnownRate.times(duration));
    lastKnownRate = new BigNumber(preprocessedPrices[i].rate);
    currentTime = priceTime;
  }
  const finalDuration = endTime.minus(currentTime);
  totalWeightedValue = totalWeightedValue.plus(
    lastKnownRate.times(finalDuration)
  );
  return totalWeightedValue;
}

// ---------------------------------------------------------------------------
// Event classification & sorting helpers
// ---------------------------------------------------------------------------

export function getUserKey(
  evt: EventTypeOnly,
  contractAddress: string
): string {
  if (evt.event === 'Swap')
    return `${evt.returnValues?.receiver?.toLowerCase()}_${contractAddress}`;
  return `${evt.address}_${contractAddress}`;
}

export function sortEventsChronologically(
  events: EventTypeOnly[]
): EventTypeOnly[] {
  return [...events].sort((a, b) => {
    if (a.blockNumber !== b.blockNumber) return a.blockNumber - b.blockNumber;
    if (a.transactionIndex !== b.transactionIndex)
      return a.transactionIndex - b.transactionIndex;
    if (a.logIndex !== b.logIndex) return a.logIndex - b.logIndex;
    return 0;
  });
}

export function isDepositEvent(
  evt: EventTypeOnly,
  tokenAddress?: string
): boolean {
  return (
    evt.event === 'Deposit' ||
    evt.event === 'Staked' ||
    (evt.event === 'Transfer' &&
      evt.returnValues?.from?.toLowerCase() === ZERO_ADDRESS &&
      !!tokenAddress) ||
    (evt.event === 'Swap' &&
      evt.returnValues?.assetOut?.toLowerCase() === tokenAddress?.toLowerCase())
  );
}

export function isWithdrawalEvent(
  evt: EventTypeOnly,
  tokenAddress?: string
): boolean {
  return (
    evt.event === 'Withdraw' ||
    evt.event === 'Withdrawn' ||
    (evt.event === 'Transfer' &&
      evt.returnValues?.to?.toLowerCase() === ZERO_ADDRESS &&
      !!tokenAddress) ||
    (evt.event === 'Swap' &&
      evt.returnValues?.assetIn?.toLowerCase() === tokenAddress?.toLowerCase())
  );
}

export function isCurrentMonth(monthStart: Date, currentDate: Date): boolean {
  return (
    monthStart.getFullYear() === currentDate.getFullYear() &&
    monthStart.getMonth() === currentDate.getMonth()
  );
}

export function getMonthBoundaries(monthStart: Date): {
  monthStartTimestamp: number;
  monthEndTimestamp: number;
} {
  const year = monthStart.getUTCFullYear();
  const month = monthStart.getUTCMonth();
  const monthStartDate = new Date(Date.UTC(year, month, 1));
  const monthEndDate = new Date(Date.UTC(year, month + 1, 1));
  return {
    monthStartTimestamp: Math.floor(monthStartDate.getTime() / 1000),
    monthEndTimestamp: Math.floor(monthEndDate.getTime() / 1000)
  };
}

// ---------------------------------------------------------------------------
// State filtering & snapshot conversion
// ---------------------------------------------------------------------------

export function filterUserHistories(userStates: UserHistories): UserHistories {
  return Object.fromEntries(
    Object.entries(userStates).filter(
      (entry) => entry[1].referral !== 'untagged' || entry[1].tvl > 0.001
    )
  );
}

export function snapshotsToTVLMap(snapshots: MonthlyReward[]): ReferralTVL {
  return snapshots.reduce((acc, item) => {
    acc[item.referralCode] = item.monthEndEligibleTVL;
    return acc;
  }, {} as ReferralTVL);
}

export function tvlMapToSnapshots(rewards: MonthlyRewards): MonthlyReward[] {
  return Object.entries(rewards).map(([code, data]) => ({
    referralCode: code,
    monthEndEligibleTVL: data.monthEndEligibleTVL,
    rewards: data.rewards,
    rewardToPay: 0
  }));
}

// ---------------------------------------------------------------------------
// Payment / reward-rate calculations
// ---------------------------------------------------------------------------

function getAllocationPercentage(farm: Farm): number {
  const farmId = farm.partnerName.toLowerCase();
  if (farmId.startsWith('sp') && !farmId.startsWith('spk')) {
    return SP_FARM_ALLOCATION;
  }
  return DEFAULT_ALLOCATION;
}

export function addPaymentCalculations(
  month: string,
  rewards: MonthlyRewards,
  farm: Farm,
  additionalPercentages: Map<string, number>
): ProcessedMonth {
  const rewardsWithPayment: MonthlyRewards = {};
  const allocationPercentage = getAllocationPercentage(farm);

  for (const [referralCode, rewardData] of Object.entries(rewards)) {
    const rewardPercentage = getRewardPercentage(
      referralCode,
      farm,
      additionalPercentages,
      month
    );

    rewardsWithPayment[referralCode] = {
      ...rewardData,
      rewardToPay: Number(
        new BigNumber(rewardData.rewards)
          .times(rewardPercentage)
          .times(allocationPercentage)
          .toFixed()
      )
    };
  }

  return { month, rewards: rewardsWithPayment };
}

async function getTransformedAdditionalPercentages(
  db: Kysely<SupabaseDB>,
  date: string
): Promise<Map<string, number>> {
  const additionalPercentages = await getAdditionalPercentagesForDate(db, date);

  const transformedPercentages = new Map<string, number>();
  for (const [refCode, additionalPercentage] of additionalPercentages) {
    transformedPercentages.set(
      refCode,
      apyToAnnualizedDailyRate(BASE_RATE + additionalPercentage)
    );
  }

  return transformedPercentages;
}

function getRewardPercentage(
  refCode: string | number,
  farm: Farm,
  additionalPercentages: Map<string, number>,
  month: string // format: 2026-01
): number {
  const code = typeof refCode === 'string' ? refCode : refCode.toString();
  const is2026OrLater = month >= '2026-01';

  // Check for 'untagged' special case
  if (
    code === 'untagged' &&
    (farm.l2 ||
      farm.partnerName === 'Sky Farm' ||
      farm.partnerName === 'Spk Farm')
  ) {
    const percentage = additionalPercentages.get('127');
    if (percentage !== undefined) {
      return percentage;
    }
  }

  // Check if we have a specific additional percentage for this ref_code
  const additionalPercentage = additionalPercentages.get(code);
  if (additionalPercentage !== undefined) {
    return additionalPercentage;
  }

  // From 2026 onwards, use NEW_DEFAULT_RATE if not in DB
  if (is2026OrLater) {
    return NEW_DEFAULT_RATE;
  }

  // Legacy logic for dates before 2026
  const numCode = parseInt(code);
  if (!isNaN(numCode) && numCode >= 100 && numCode < 1000) return SPARK_RATE;
  if (
    code === 'untagged' &&
    (farm.l2 ||
      farm.partnerName === 'Sky Farm' ||
      farm.partnerName === 'Spk Farm')
  )
    return SPARK_RATE;
  return NON_SPARK_REF_RATE;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function rebuildReferralTVL(userStates: BNUserStates): BNReferralTVL {
  const referralTVL: BNReferralTVL = {};

  for (const userId in userStates) {
    const { referral, tvl } = userStates[userId];
    referralTVL[referral] = (referralTVL[referral] ?? BN_ZERO).plus(tvl);
  }

  return referralTVL;
}
