const KEY = 'tcg.searchHistory';
export function readHistory(storage, userId) {
  try {
    const value = JSON.parse(storage.getItem(userId ? `${KEY}.${userId}` : KEY) ?? '[]');
    return Array.isArray(value) ? value.filter(item => typeof item === 'string').reduce(appendHistory, []) : [];
  } catch { return []; }
}
export function appendHistory(history, keyword) {
  const value = keyword.trim();
  return value ? [...history.filter(item => item !== value), value].slice(-10) : history;
}
export function saveHistory(storage, history, userId) {
  try { storage.setItem(userId ? `${KEY}.${userId}` : KEY, JSON.stringify(history)); } catch { /* Search still works when storage is unavailable. */ }
}
