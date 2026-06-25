import BigNumber from 'bignumber.js';
import { config } from '@/update-distribution-rewards-calculations/calculations/config.ts';
import { DateTime } from 'luxon';
import { TokenPrice } from './models/models.ts';
import { getDepositRatesTimeSeries } from '@/update-distribution-rewards-calculations/queries.ts';
import { Kysely } from 'kysely';
import {SupabaseDB} from "../../db/schema.ts";

interface TokenWithPrices {
  prices: TokenPrice[];
}

async function fetchTokenPrices(
  address: string,
  startDate: DateTime,
  endDate: DateTime
): Promise<TokenPrice[]> {
  const url = config.tokenPrices.dataProviderURL;
  const network = config.tokenPrices.network;

  const startTime = startDate.startOf('day').toISO();

  const endTime = endDate.endOf('day').toISO();

  const requestBody = {
    address,
    network,
    startTime,
    endTime
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data: { data: { timestamp: number; value: string }[] } =
      await response.json();

    return Array.isArray(data?.data)
      ? data.data.map((item) => ({
          timestamp: item.timestamp,
          rate: new BigNumber(item.value)
        }))
      : [];
  } catch (error) {
    console.error(
      'Error fetching token prices:',
      (error as Error).message || error
    );
    return [];
  }
}

export function mergeAndCreateRatio(
  token1Data: TokenPrice[],
  token2Data: TokenPrice[]
): TokenPrice[] {
  const prices1 = token1Data || [];
  const prices2 = token2Data || [];

  if (prices1.length === 0 && prices2.length === 0) return [];

  const result: TokenPrice[] = [];
  let i = 0,
    j = 0;
  let lastRate1 = new BigNumber('1');
  let lastRate2 = new BigNumber('1');

  while (i < prices1.length || j < prices2.length) {
    const ts1 =
      i < prices1.length
        ? typeof prices1[i].timestamp === 'string'
          ? DateTime.fromISO(prices1[i].timestamp as string, {
              zone: 'utc'
            }).toSeconds()
          : (prices1[i].timestamp as number)
        : null;

    const ts2 =
      j < prices2.length
        ? typeof prices2[j].timestamp === 'string'
          ? DateTime.fromISO(prices2[j].timestamp as string, {
              zone: 'utc'
            }).toSeconds()
          : (prices2[j].timestamp as number)
        : null;

    let timestamp: number;

    if (ts1 === null) {
      timestamp = ts2 as number;
      lastRate2 = new BigNumber(prices2[j].rate ?? '1');
      j++;
    } else if (ts2 === null) {
      timestamp = ts1 as number;
      lastRate1 = new BigNumber(prices1[i].rate ?? '1');
      i++;
    } else if (ts1 < ts2) {
      timestamp = ts1;
      lastRate1 = new BigNumber(prices1[i].rate ?? '1');
      i++;
    } else if (ts2 < ts1) {
      timestamp = ts2;
      lastRate2 = new BigNumber(prices2[j].rate ?? '1');
      j++;
    } else {
      timestamp = ts1;
      lastRate1 = new BigNumber(prices1[i].rate ?? '1');
      lastRate2 = new BigNumber(prices2[j].rate ?? '1');
      i++;
      j++;
    }

    result.push({
      timestamp,
      rate: lastRate2.dividedBy(lastRate1)
    });
  }

  return result;
}

const MS_IN_A_YEAR = 365 * 86400 * 1000;

export async function getDepositBasedTokenRatio(
  db: Kysely<SupabaseDB>,
  tokenLabel: string
) {
  const tokenCodes = config.tokenPrices.tokenCodes;

  const tokenCode = tokenCodes.find((token) => token.name === tokenLabel);
  const result = await getDepositRatesTimeSeries(
    db,
    'ethereum',
    tokenCode!.tokenAddress
  );
  return result.map((rate) => {
    return {
      timestamp: Number(rate.timestamp),
      rate: rate.rate
    };
  });
}

export async function fetchAndMergeTokenPrices(
  token1: string,
  token2: string
): Promise<TokenPrice[]> {
  const tokens = config.tokenPrices.tokenCodes.filter((code) =>
    [token1, token2].includes(code.name)
  );
  const end = DateTime.utc();
  const start = end.minus({ milliseconds: MS_IN_A_YEAR });

  const arr: TokenWithPrices[] = await Promise.all(
    tokens.map(async (token) => ({
      prices: await fetchTokenPrices(token.tokenAddress, start, end)
    }))
  );

  const usdsIdx = tokens.findIndex((t) => t.name === token1);
  const susdsIdx = tokens.findIndex((t) => t.name === token2);
  const usdsData = arr[usdsIdx] ?? { prices: [] };
  const susdsData = arr[susdsIdx] ?? { prices: [] };

  return mergeAndCreateRatio(usdsData.prices, susdsData.prices);
}
