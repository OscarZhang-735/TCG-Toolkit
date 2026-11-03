export async function fetchCardExport(api, format, signal) {
  const response = await api(`/api/cards/export?format=${encodeURIComponent(format)}`, {cache: 'no-store', signal});
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
