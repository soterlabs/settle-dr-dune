import BigNumber from 'bignumber.js';
export interface EventReturnValues {
  shares?: string | number;
  amount?: string | number;
  assets?: string | number;
  value?: string | number;
  user?: string;
  owner?: string;
  receiver?: string;
  from?: string;
  to?: string;
  assetIn?: string;
  assetOut?: string;
  [key: string]: unknown;
}
export interface Event {
  event: string;
  amount: number | null;
  address: string | null;
  referral: string;
  blockTimestamp: number;
  blockNumber: number;
  transactionHash: string;
  returnValues: EventReturnValues;
  contractAddress: string;
  logIndex: number;
  transactionIndex: number;
}
export interface UserState {
  tvl: number;
  referral: string;
  hasBeenTagged: boolean;
}
export interface ReferralTVL {
  [referralCode: string]: number;
}
export interface MonthlyReward {
  referralCode: string;
  monthEndEligibleTVL: number;
  rewards: number;
  rewardToPay: number;
}
export interface MonthlyRewards {
  [referralCode: string]: MonthlyReward;
}
export interface ProcessedMonth {
  month: string;
  rewards: MonthlyRewards;
  userHistories?: Record<string, UserState>;
}

export interface PreviousMonthState {
  userStates: { [userKey: string]: UserState };
  referralTVL: ReferralTVL;
}
export interface TokenPrice {
  timestamp: string | number;
  rate: BigNumber;
}
export interface Farm {
  partnerName: string;
  network: string;
  contractAddress: string;
  tokenAddress?: string;
  tokenCode?: string;
  l2?: boolean;
  monitoredEvents?: string[];
}
export interface BlockTimestampRecord {
  blockNumber: number;
  timestamp: number;
  network: string;
}
export type UserHistories = Record<string, UserState>;
