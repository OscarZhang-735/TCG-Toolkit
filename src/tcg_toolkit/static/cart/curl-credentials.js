// Parse text only: no shell evaluation, network requests, or persistence here.
function invalid() { throw new Error('cart_curl_invalid'); }
function words(text) {
  const tokens = [];
  let word = '', quote = '', active = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '\\' && quote !== "'") {
      const next = text[++i];
      if (next === undefined) invalid();
      if (next === '\n') continue;
      if (next === '\r' && text[i + 1] === '\n') { i++; continue; }
      word += next; active = true; continue;
    }
    if (quote) {
      if (c === quote) quote = '';
      else word += c;
    } else if (c === '"' || c === "'") { quote = c; active = true; }
    else if (/\s/.test(c)) {
      if (active) { tokens.push(word); word = ''; active = false; }
    } else {
      if (';|&<>`'.includes(c)) invalid();
      word += c; active = true;
    }
  }
  if (quote) invalid();
  if (active) tokens.push(word);
  return tokens;
}

export function parseCurlCredentials(input) {
  if (typeof input !== 'string' || input.length > 65536 || input.includes('\0')) invalid();
  // Chrome Copy as cURL (cmd) uses caret-escaped quotes and line continuations.
  const cmd = /\^\r?\n|\^"/.test(input);
  const args = words(cmd ? input.replace(/\^\r?\n/g, '').replace(/\^(["^])/g, '$1') : input);
  if (!/^curl(?:\.exe)?$/i.test(args.shift() || '')) invalid();
  const cookies = [], tokens = [];
  for (let i = 0; i < args.length; i++) {
    let flag = args[i], value;
    const equal = flag.match(/^(--header|--cookie)=(.*)$/s);
    if (equal) { flag = equal[1]; value = equal[2]; }
    else if (['-H', '--header', '-b', '--cookie'].includes(flag)) value = args[++i];
    else if (/^-[Hb].+/.test(flag)) { value = flag.slice(2); flag = flag.slice(0, 2); }
    else continue;
    if (typeof value !== 'string') invalid();
    if (flag === '-b' || flag === '--cookie') cookies.push(value);
    else {
      const header = value.match(/^([^:]+):\s*(.*)$/s);
      if (!header) invalid();
      if (header[1].toLowerCase() === 'cookie') cookies.push(header[2]);
      if (header[1].toLowerCase() === 'x-csrf-token') tokens.push(header[2].trim());
    }
  }
  if (cookies.length !== 1 || tokens.length !== 1) invalid();
  const sessions = cookies[0].split(';').map(s => s.trim()).filter(s => s.startsWith('laravel_session='));
  if (sessions.length !== 1) invalid();
  const cookie = sessions[0].slice('laravel_session='.length), csrf_token = tokens[0];
  const safe = /^[A-Za-z0-9%+/_=.\-]+$/;
  if (!cookie || cookie.length > 16384 || !safe.test(cookie) || !csrf_token || csrf_token.length > 4096 || !safe.test(csrf_token)) invalid();
  return {cookie, csrf_token};
}
