/**
 * Utilidades puras de sanitización, formato, URLs, hashing local y notificaciones.
 */

export const $ = (id) => document.getElementById(id);

export function escapeHTML(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function sanitizeHttpUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') return '#';
  const trimmed = rawUrl.trim();
  try {
    const base = window.location.origin && window.location.origin !== 'null'
      ? window.location.origin
      : undefined;
    const parsed = base ? new URL(trimmed, base) : new URL(trimmed);
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
      return parsed.href;
    }
  } catch {
    // invalid URL
  }
  return '#';
}

export function sanitizeBgColor(rawColor) {
  if (typeof rawColor === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(rawColor.trim())) {
    return rawColor.trim();
  }
  return '#f3efe9';
}

export function formatCLP(amount) {
  return '$' + Math.round(Number(amount) || 0).toLocaleString('es-CL');
}

export function normalizeText(str) {
  return (str || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function getStoreById(stores, storeId) {
  return stores.find(s => s.id === storeId) || {
    id: storeId,
    name: storeId,
    cls: 'store-mercadolibre',
    searchBase: 'https://www.google.cl/search?q='
  };
}

export function buildStoreUrl(stores, item) {
  if (item.url) return sanitizeHttpUrl(item.url);
  const store = getStoreById(stores, item.storeId);
  const q = encodeURIComponent(item.title || '');
  if (store.id === 'mercadolibre') {
    return sanitizeHttpUrl(store.searchBase + encodeURIComponent((item.title || '').replace(/\s+/g, '-')));
  }
  return sanitizeHttpUrl(store.searchBase + q);
}

export async function hashLocalPassword(password, salt) {
  const useSalt = salt || Array.from(crypto.getRandomValues(new Uint8Array(12)))
    .map(b => b.toString(16).padStart(2, '0')).join('');
  if (window.crypto && window.crypto.subtle) {
    const data = new TextEncoder().encode(useSalt + ':' + password);
    const buf = await crypto.subtle.digest('SHA-256', data);
    const hex = Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
    return `sha256$${useSalt}$${hex}`;
  }
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  const input = useSalt + ':' + password;
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ (c + i), 0x85ebca6b) >>> 0;
  }
  return `fnv$${useSalt}$${h1.toString(16).padStart(8, '0')}${h2.toString(16).padStart(8, '0')}`;
}

export async function verifyLocalPassword(password, storedRecord) {
  if (!storedRecord) return false;
  if (storedRecord.passwordHash && (storedRecord.passwordHash.startsWith('sha256$') || storedRecord.passwordHash.startsWith('fnv$'))) {
    const parts = storedRecord.passwordHash.split('$');
    if (parts.length === 3) {
      const computed = await hashLocalPassword(password, parts[1]);
      return computed === storedRecord.passwordHash;
    }
  }
  return typeof storedRecord.password === 'string' && storedRecord.password === password;
}

let toastTimer = null;
export function showToast(msg) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3200);
}
