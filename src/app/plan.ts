// Plan limits shown in the interface. Every account is PRO until billing and server-side entitlements arrive (W18).
export const USER_TIER = 'PRO' as 'Free' | 'PLUS' | 'PRO';

export const batchLimit = (stage: 'abstract' | 'fulltext') => {
  if (stage === 'abstract') return USER_TIER === 'PRO' ? 200 : USER_TIER === 'PLUS' ? 100 : 20;
  return USER_TIER === 'PRO' ? 200 : USER_TIER === 'PLUS' ? 100 : 50;
};
