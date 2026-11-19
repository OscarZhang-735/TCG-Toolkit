// Each game has its own rarity system; order is category-local display order.
export const rarityCategories = ['Normal', 'Rare', 'Special'];
export const raritySystems = {
  sev: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'BR', category: 'Normal', english: 'Bronze Rare', chinese: '铜稀有卡', order: 1},
    {code: 'SR', category: 'Normal', english: 'Silver Rare', chinese: '银稀有卡', order: 2},
    {code: 'GR', category: 'Normal', english: 'Gold Rare', chinese: '金稀有卡', order: 3},
    {code: 'LG', category: 'Normal', english: 'Legendary', chinese: '传说卡', order: 4},
    // Parallel entries use display order, not pull-rate or price ranking.
    {code: 'Premium', category: 'Rare', english: 'Premium', chinese: '闪卡加工版', order: 1},
    {code: 'P', category: 'Rare', english: 'Premium', chinese: '闪卡加工版', order: 1},
    {code: 'SL', category: 'Rare', english: 'Super Legendary', chinese: '超传说', order: 2},
    {code: 'UR', category: 'Rare', english: 'Ultimate Rare', chinese: '终极稀有', order: 3},
    {code: 'SP', category: 'Rare', english: 'Special', chinese: '特别稀有', order: 4},
    {code: 'SSP', category: 'Rare', english: 'Super Special', chinese: '超特别稀有', order: 5},
    {code: 'GBF', category: 'Rare', english: 'Granblue Fantasy Rare', chinese: '碧蓝幻想稀有', order: 6},
    // Distribution, card types and treatments are grouped separately.
    {code: 'PR', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 1},
    {code: 'SD', category: 'Special', english: 'Starter Deck', chinese: '起始卡组', order: 2},
    {code: 'LD', category: 'Special', english: 'Leader Card', chinese: '主战者卡', order: 3},
    {code: 'Leader', category: 'Special', english: 'Leader Card', chinese: '主战者卡', order: 3},
    {code: 'Token', category: 'Special', english: 'Token Card', chinese: '衍生物卡', order: 4},
    {code: 'EP', category: 'Special', english: 'Evolution Point', chinese: '进化点卡', order: 5},
    {code: 'SEP', category: 'Special', english: 'Super Evolution Point', chinese: '超进化点卡', order: 6},
    {code: 'Serial', category: 'Special', english: 'Serial Numbered', chinese: '序列编号卡', order: 7},
    {code: 'Collaboration', category: 'Special', english: 'Collaboration Special', chinese: '联动特殊卡', order: 8},
  ],
  sev_en: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'B', category: 'Normal', english: 'Bronze Rare', chinese: '铜稀有卡', order: 1},
    {code: 'S', category: 'Normal', english: 'Silver Rare', chinese: '银稀有卡', order: 2},
    {code: 'G', category: 'Normal', english: 'Gold Rare', chinese: '金稀有卡', order: 3},
    {code: 'L', category: 'Normal', english: 'Legendary', chinese: '传说卡', order: 4},
    // Parallel entries use display order, not pull-rate or price ranking.
    {code: 'Premium', category: 'Rare', english: 'Premium', chinese: '闪卡加工版', order: 1},
    {code: 'P', category: 'Rare', english: 'Premium', chinese: '闪卡加工版', order: 1},
    {code: 'SL', category: 'Rare', english: 'Super Legendary', chinese: '超传说', order: 2},
    {code: 'U', category: 'Rare', english: 'Ultimate Rare', chinese: '终极稀有', order: 3},
    {code: 'SP', category: 'Rare', english: 'Special', chinese: '特别稀有', order: 4},
    {code: 'SSP', category: 'Rare', english: 'Super Special', chinese: '超特别稀有', order: 5},
    {code: 'GBF', category: 'Rare', english: 'Granblue Fantasy Rare', chinese: '碧蓝幻想稀有', order: 6},
    // Distribution, card types and treatments are grouped separately.
    {code: 'PR', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 1},
    {code: 'SD', category: 'Special', english: 'Starter Deck', chinese: '起始卡组', order: 2},
    {code: 'LD', category: 'Special', english: 'Leader Card', chinese: '主战者卡', order: 3},
    {code: 'Leader', category: 'Special', english: 'Leader Card', chinese: '主战者卡', order: 3},
    {code: 'Token', category: 'Special', english: 'Token Card', chinese: '衍生物卡', order: 4},
    {code: 'EP', category: 'Special', english: 'Evolution Point', chinese: '进化点卡', order: 5},
    {code: 'SEP', category: 'Special', english: 'Super Evolution Point', chinese: '超进化点卡', order: 6},
    {code: 'Serial', category: 'Special', english: 'Serial Numbered', chinese: '序列编号卡', order: 7},
    {code: 'Collaboration', category: 'Special', english: 'Collaboration Special', chinese: '联动特殊卡', order: 8},
  ],
  ws_en: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'C', category: 'Normal', english: 'Common', chinese: '普通', order: 1},
    {code: 'U', category: 'Normal', english: 'Uncommon', chinese: '非普通', order: 2},
    {code: 'R', category: 'Normal', english: 'Rare', chinese: '稀有', order: 3},
    {code: 'RR', category: 'Normal', english: 'Double Rare', chinese: '双重稀有', order: 4},
    {code: 'CC', category: 'Normal', english: 'Climax Common', chinese: '普通高潮卡', order: 5},
    {code: 'CR', category: 'Normal', english: 'Climax Rare', chinese: '稀有高潮卡', order: 6},
    {code: 'SR', category: 'Rare', english: 'Super Rare', chinese: '常规特殊闪卡', order: 1},
    {code: 'RRR', category: 'Rare', english: 'Triple Rare', chinese: '三重稀有平行卡', order: 2},
    {code: 'RRR+', category: 'Rare', english: 'Triple Rare Plus', chinese: '特殊增强版 RRR', order: 3},
    {code: 'OFR', category: 'Rare', english: 'Over Frame Rare', chinese: '越框设计的平行卡', order: 4},
    {code: 'SP', category: 'Rare', english: 'Special', chinese: '特别稀有卡', order: 5},
    {code: 'SSP', category: 'Rare', english: 'Super Special', chinese: '超特别稀有卡', order: 6},
    {code: 'SEC', category: 'Rare', english: 'Secret', chinese: '秘密稀有卡', order: 7},
    {code: 'SEC+', category: 'Rare', english: 'Secret Plus', chinese: '更高规格秘密稀有卡', order: 8},
    {code: 'SIR', category: 'Rare', english: 'Serial Rare', chinese: '序列编号稀有卡', order: 9},
    {code: 'ENX', category: 'Rare', english: 'English Exclusive', chinese: '英文版独占特殊插画卡', order: 10},
    {code: 'TD', category: 'Special', english: 'Trial Deck', chinese: '预组卡', order: 1},
    {code: 'PR', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 2},
    {code: 'TD+', category: 'Special', english: 'Trial Deck Plus', chinese: '强化预组', order: 3},
  ],
  ws: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'C', category: 'Normal', english: 'Common', chinese: '普通卡', order: 1},
    {code: 'U', category: 'Normal', english: 'Uncommon', chinese: '非普通卡', order: 2},
    {code: 'R', category: 'Normal', english: 'Rare', chinese: '稀有卡', order: 3},
    {code: 'RR', category: 'Normal', english: 'Double Rare', chinese: '双重稀有卡', order: 4},
    {code: 'RR+', category: 'Normal', english: 'Double Rare Plus', chinese: '双重稀有 Plus', order: 5},
    {code: 'CC', category: 'Normal', english: 'Climax Common', chinese: 'Climax 普通卡', order: 6},
    {code: 'CR', category: 'Normal', english: 'Climax Rare', chinese: 'Climax 稀有卡', order: 7},
    {code: 'SR', category: 'Rare', english: 'Super Rare', chinese: '超稀有闪卡', order: 1},
    {code: 'RRR', category: 'Rare', english: 'Triple Rare', chinese: '三重稀有卡', order: 2},
    {code: 'RRR+', category: 'Rare', english: 'Triple Rare Plus', chinese: '三重稀有 Plus', order: 3},
    {code: 'OFR', category: 'Rare', english: 'Over Frame Rare', chinese: '特殊边框稀有卡', order: 4},
    {code: 'SP', category: 'Rare', english: 'Special', chinese: '特别稀有卡', order: 5},
    {code: 'SSP', category: 'Rare', english: 'Super Special', chinese: '超特别稀有卡', order: 6},
    {code: 'SEC', category: 'Rare', english: 'Secret', chinese: '秘密稀有卡', order: 7},
    {code: 'SEC+', category: 'Rare', english: 'Secret Plus', chinese: '秘密稀有 Plus', order: 8},
    {code: 'AGR', category: 'Rare', english: 'Autograph Rare', chinese: '亲笔签名稀有卡', order: 9},
    {code: 'SIR', category: 'Rare', english: 'Serial Rare', chinese: '序列编号稀有卡', order: 10},
    {code: 'TSP', category: 'Rare', english: 'Theater Special', chinese: '剧场主题特别卡', order: 11},
    {code: 'GGR', category: 'Rare', english: 'Gun Gale Rare', chinese: 'GGO 专属稀有卡', order: 12},
    {code: 'SWR', category: 'Rare', english: 'Star Wars Rare', chinese: '星球大战专属稀有卡', order: 13},
    {code: 'BDR', category: 'Rare', english: 'Band Rare', chinese: '乐队主题特殊卡', order: 14},
    {code: 'FBR', category: 'Rare', english: 'Fujimi Bunko Rare', chinese: '富士见 Fantasia 文库特殊卡', order: 15},
    {code: 'JJR', category: 'Rare', english: 'JoJo Rare', chinese: 'JOJO 主题特殊卡', order: 16},
    {code: 'XR', category: 'Rare', english: 'Extra Rare', chinese: '特殊稀有卡', order: 17},
    {code: 'HC', category: 'Rare', english: 'Holo Common', chinese: 'C 等级的闪卡版本', order: 18},
    {code: 'HU', category: 'Rare', english: 'Holo Uncommon', chinese: 'U 等级的闪卡版本', order: 19},
    {code: 'HR', category: 'Rare', english: 'Holo Rare', chinese: 'R 等级的闪卡版本', order: 20},
    {code: 'HRR', category: 'Rare', english: 'Holo Double Rare', chinese: 'RR 等级的闪卡版本', order: 21},
    {code: 'SPM', category: 'Rare', english: 'Special Pack Rare', chinese: '特殊卡包稀有卡', order: 22},
    {code: 'STR', category: 'Rare', english: 'Starlight Rare', chinese: '星光主题稀有卡', order: 23},
    {code: 'TD', category: 'Special', english: 'Trial Deck', chinese: '预组卡', order: 1},
    {code: 'PR', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 2},
  ],
  chaos: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'C', category: 'Normal', english: 'Common', chinese: '普通卡', order: 1},
    {code: 'U', category: 'Normal', english: 'Uncommon', chinese: '非普通卡', order: 2},
    {code: 'R', category: 'Normal', english: 'Rare', chinese: '稀有卡', order: 3},
    {code: 'RR', category: 'Normal', english: 'Double Rare', chinese: '双重稀有卡', order: 4},
    {code: 'SC', category: 'Rare', english: 'Secret Common', chinese: '特殊版本', order: 1},
    {code: 'SR', category: 'Rare', english: 'Special Rare', chinese: '特殊闪卡', order: 2},
    {code: 'RRR', category: 'Rare', english: 'Triple Rare', chinese: '三重稀有平行卡', order: 3},
    {code: 'SRR', category: 'Rare', english: 'SRR', chinese: '特殊高罕标记', order: 4},
    {code: 'SRRR', category: 'Rare', english: 'SRRR', chinese: '特殊高罕标记', order: 5},
    {code: 'SP', category: 'Rare', english: 'Special', chinese: '特别稀有', order: 6},
    {code: 'SSP', category: 'Rare', english: 'Super Special', chinese: '超特别稀有', order: 7},
    {code: 'GR', category: 'Rare', english: 'GR', chinese: '特殊高罕标记', order: 8},
    {code: 'IMR', category: 'Rare', english: 'IMR', chinese: '特殊高罕标记', order: 9},
    {code: 'ISR', category: 'Rare', english: 'ISR', chinese: '特殊高罕标记', order: 10},
    {code: 'BR', category: 'Rare', english: 'BR', chinese: '特殊高罕标记', order: 11},
    {code: 'EXR', category: 'Rare', english: 'EXR', chinese: '特殊高罕标记', order: 12},
    {code: 'ETMR', category: 'Rare', english: 'ETMR', chinese: '特殊高罕标记', order: 13},
    {code: 'ETMSP', category: 'Rare', english: 'ETMSP', chinese: '特殊高罕标记', order: 14},
    {code: 'SE', category: 'Rare', english: 'SE', chinese: '特殊版本标记', order: 15},
    {code: 'T', category: 'Special', english: 'Trial Deck', chinese: '预组卡', order: 1},
    {code: 'S', category: 'Special', english: 'Starting Deck', chinese: '起始牌组卡', order: 2},
    {code: 'PR', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 3},
    {code: 'P', category: 'Special', english: 'Partner', chinese: '伙伴卡', order: 4},
    {code: 'PG', category: 'Special', english: 'PG', chinese: '特殊版本', order: 5},
  ],
  lycee: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'C', category: 'Normal', english: 'Common', chinese: '普通卡', order: 1},
    {code: 'U', category: 'Normal', english: 'Uncommon', chinese: '非普通卡', order: 2},
    {code: 'R', category: 'Normal', english: 'Rare', chinese: '稀有卡', order: 3},
    {code: 'SR', category: 'Normal', english: 'Super Rare', chinese: '超稀有卡', order: 4},
    // Parallel rarities do not have a strict ranking across all series.
    {code: 'KC', category: 'Rare', english: 'KC', chinese: '特殊闪卡版本', order: 1},
    {code: 'KR', category: 'Rare', english: 'KR', chinese: 'R 的特殊闪卡版本', order: 2},
    {code: 'KSR', category: 'Rare', english: 'KSR', chinese: 'SR 的特殊加工版本', order: 3},
    {code: 'CP', category: 'Rare', english: 'CP', chinese: '特殊平行卡', order: 4},
    {code: 'SP', category: 'Rare', english: 'SP', chinese: '特别签名卡', order: 5},
    {code: 'MSP', category: 'Rare', english: 'MSP', chinese: '特殊 SP 版本', order: 6},
    {code: 'SSP', category: 'Rare', english: 'SSP', chinese: '超特别签名卡', order: 7},
    {code: 'SSR', category: 'Rare', english: 'SSR', chinese: '特别加工 SR 版本', order: 8},
    {code: 'ST', category: 'Special', english: 'Starter', chinese: '预组卡', order: 1},
    {code: 'P', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 2},
    {code: 'L', category: 'Special', english: 'Lucky', chinese: '幸运卡', order: 3},
    {code: 'ER', category: 'Special', english: 'ER', chinese: '特殊收录卡', order: 4},
  ],
  wsr: [
    {code: 'Unknown', category: 'Normal', english: 'None', chinese: '无', order: 0},
    {code: 'C', category: 'Normal', english: 'Common', chinese: '普通卡', order: 1},
    {code: 'R', category: 'Normal', english: 'Rare', chinese: '稀有卡', order: 2},
    {code: 'RR', category: 'Normal', english: 'Double Rare', chinese: '双重稀有卡', order: 3},
    {code: 'SR', category: 'Rare', english: 'Super Rare', chinese: '超稀有闪卡', order: 1},
    {code: 'OFR', category: 'Rare', english: 'Over Frame Rare', chinese: '特殊边框稀有卡', order: 2},
    {code: 'SP', category: 'Rare', english: 'Special', chinese: '特别稀有卡', order: 3},
    {code: 'SSP', category: 'Rare', english: 'Super Special', chinese: '超特别稀有卡', order: 4},
    {code: 'SEC', category: 'Rare', english: 'Secret', chinese: '秘密稀有卡', order: 5},
    {code: 'AGR', category: 'Rare', english: 'Autograph Rare', chinese: '亲笔签名稀有卡', order: 6},
    {code: 'TD', category: 'Special', english: 'Trial Deck', chinese: '预组卡组', order: 1},
    {code: 'PR', category: 'Special', english: 'Promotional', chinese: '宣传卡', order: 2},
    {code: 'PR+', category: 'Special', english: 'Promotional Plus', chinese: '宣传卡 Plus', order: 3},
  ],
};

export function raritiesForGame(game) {
  const key = game?.trim().toLowerCase();
  const system = ({chaostcg: 'chaos', 'sev en': 'sev_en', 'ws en': 'ws_en'})[key] ?? key;
  return Object.hasOwn(raritySystems, system) ? raritySystems[system] : [];
}

// Missing data stays unmapped; Unknown is an explicit rarity code.
export function findRarity(game, code) {
  return raritiesForGame(game).find(entry => entry.code.toLowerCase() === code?.trim().toLowerCase()) ?? null;
}

// Badge tier for display: 'normal', 'rare' or 'special'; unmapped codes look normal.
export function rarityTier(game, code) {
  return (findRarity(game, code)?.category ?? 'Normal').toLowerCase();
}

// Hover text for a badge, e.g. "SEC · Secret"; null when the code is not mapped.
export function rarityDescription(game, code, language) {
  const entry = findRarity(game, code);
  if (!entry) return null;
  return `${entry.code} · ${language.startsWith('zh') ? entry.chinese : entry.english}`;
}
