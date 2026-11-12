// Columns the server can export, in file order.
export const EXPORT_FIELDS = ['game', 'card_number', 'rarity', 'name', 'quantity', 'notes', 'group', 'image_url', 'id', 'created_at', 'updated_at'];

// Query string for /api/cards/export. Omitted games/fields/group mean "everything";
// language only affects XLSX headings ('keys' keeps raw field names).
export function exportQuery({format, language = 'keys', games = null, fields = null, group = null}) {
  const params = new URLSearchParams({format});
  if (group) params.set('group_id', group);
  if (format === 'xlsx' && language !== 'keys') params.set('language', language);
  if (games) for (const game of games) params.append('games', game);
  if (fields && fields.length < EXPORT_FIELDS.length) {
    for (const field of EXPORT_FIELDS) if (fields.includes(field)) params.append('fields', field);
  }
  return params.toString();
}

export async function fetchCardExport(api, options, signal) {
  const response = await api(`/api/cards/export?${exportQuery(options)}`, {cache: 'no-store', signal});
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail === 'export_xlsx_text_too_long' ? 'cards_export_too_long' : 'cards_export_failed');
  }
  return response.blob();
}

export function downloadCardExport(blob, format) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url;
  link.download = `cards-${new Date().toISOString().slice(0, 10)}.${format}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
