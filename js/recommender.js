/**
 * Motor de recomendación, carga de reglas compartidas (data/domain-rules.json),
 * scoring, filtrado coherente y control de concurrencia (AbortController + searchSeq).
 */

import { normalizeText } from './utils.js';

let rules = {
  stopwords: new Set(),
  phraseSynonyms: {},
  childRecipients: new Set(['bebe_0_2', 'nino_3_4', 'nino', 'adolescente']),
  kidsRe: null,
  foodRe: null,
  exemptRe: null
};

export async function loadDomainRules() {
  try {
    const res = await fetch('data/domain-rules.json');
    if (!res.ok) return;
    const data = await res.json();
    rules = {
      stopwords: new Set(data.stopwords || []),
      phraseSynonyms: data.phraseSynonyms || {},
      childRecipients: new Set(data.childRecipients || []),
      kidsRe: new RegExp(`\\b(?:${(data.kidsPatterns || []).join('|')})\\b`),
      foodRe: new RegExp(`\\b(?:${(data.nonGiftableFoodPatterns || []).join('|')})\\b`),
      exemptRe: new RegExp(`\\b(?:${(data.giftUtensilExemptPatterns || []).join('|')})\\b`)
    };
  } catch {
    // fallback silencioso si se abre por file:// sin fetch local
  }
}

export function scoreProduct(item, query, interestsCatalog) {
  let score = 30;
  const reasons = [];

  if (query.stores.length && !query.stores.includes(item.storeId)) return null;

  const tNorm = normalizeText(item.title);

  // Filtro de alimentos no regalables (exime utensilios/sets como "tabla de quesos", "molde de galletas", "aceite esencial")
  if (rules.foodRe && rules.foodRe.test(tNorm)) {
    const isSupermarket = item.storeId === 'jumbo' || item.storeId === 'santaisabel';
    if (isSupermarket || !(rules.exemptRe && rules.exemptRe.test(tNorm))) {
      return null;
    }
  }

  // Filtro de juguetes infantiles para destinatarios adultos
  const isAdult = query.recipient !== 'cualquiera' && !rules.childRecipients.has(query.recipient);
  const wantsKids = query.interests.includes('juguetes') || query.interests.includes('bebes');
  if (isAdult && !wantsKids && rules.kidsRe && rules.kidsRe.test(tNorm)) {
    return null;
  }

  const { budgetMin, budgetMax } = query;
  if (item.price >= budgetMin && item.price <= budgetMax) {
    score += 30;
    reasons.push('dentro de tu presupuesto');
  } else if (item.price < budgetMin && item.price >= budgetMin * 0.65) {
    score += 12;
    reasons.push('opción más económica');
  } else if (item.price > budgetMax && item.price <= budgetMax * 1.25) {
    score += 6;
    reasons.push('ligeramente sobre el tope');
  } else {
    score -= 30;
  }

  const matchedInterests = (item.interests || []).filter(i => query.interests.includes(i));
  if (matchedInterests.length > 0) {
    score += Math.min(28, matchedInterests.length * 16);
    const labels = matchedInterests
      .map(id => interestsCatalog.find(x => x.id === id)?.label)
      .filter(Boolean);
    reasons.push('coincide con ' + labels.slice(0, 2).join(' y '));
  }

  // Coincidencia con referencia libre (título + keywords del catálogo demo)
  const refNorm = normalizeText(query.reference);
  if (refNorm) {
    const tokens = refNorm.split(' ').filter(t => t.length >= 3 && !rules.stopwords.has(t));
    const haystack = item.source === 'demo'
      ? normalizeText(item.title + ' ' + (item.keywords || []).join(' '))
      : tNorm;
    let kwHits = 0;
    tokens.forEach(tok => {
      const mapped = rules.phraseSynonyms[tok] ? rules.phraseSynonyms[tok].split(' ') : [tok];
      const stems = mapped.map(m => (m.length >= 6 ? m.slice(0, 6) : m));
      if (stems.some(s => haystack.includes(s))) kwHits++;
    });
    if (kwHits > 0) {
      score += Math.min(34, kwHits * 18);
      reasons.push('calza directo con tu referencia');
    } else if (tokens.length > 0 && !(item.matchedTerms && item.matchedTerms.length)) {
      score -= 20;
    }
  }

  if (query.recipient !== 'cualquiera' && (item.recipients || []).includes(query.recipient)) score += 6;
  if (query.occasion !== 'cualquiera' && (item.occasions || []).includes(query.occasion)) score += 6;

  score += Math.round(((Number(item.rating) || 4.5) - 4.0) * 5);
  if (item.source === 'live') score += 6;

  return {
    ...item,
    score,
    matchPct: Math.max(55, Math.min(99, Math.round(score))),
    reasonText: reasons.length
      ? 'Ideal porque está ' + reasons.slice(0, 2).join(' y ') + '.'
      : 'Muy bien evaluado y popular como regalo en Chile.'
  };
}

export function rankAndDiversify(rawItems, query, interestsCatalog) {
  const seenKeys = new Set();
  const deduped = rawItems.filter(item => {
    const k = item.storeId + '::' + normalizeText(item.title);
    if (seenKeys.has(k)) return false;
    seenKeys.add(k);
    return true;
  });

  const scored = deduped
    .map(item => scoreProduct(item, query, interestsCatalog))
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);

  const minThreshold = scored.length ? Math.max(48, scored[0].score - 28) : 48;
  const coherent = scored.filter((item, idx) => idx < 3 || item.score >= minThreshold);

  const topScore = coherent.length ? coherent[0].score : 0;
  const top3 = [];
  const usedStores = new Set();
  const remaining = [];

  for (const item of coherent) {
    if (top3.length < 3 && !usedStores.has(item.storeId) && item.score >= topScore - 12) {
      top3.push(item);
      usedStores.add(item.storeId);
    } else {
      remaining.push(item);
    }
  }
  while (top3.length < 3 && remaining.length > 0) {
    top3.push(remaining.shift());
  }

  return [...top3, ...remaining];
}

let activeController = null;
let searchSeq = 0;

export async function fetchLiveScrape(payload) {
  if (activeController) activeController.abort();
  activeController = new AbortController();
  const seq = ++searchSeq;

  const res = await fetch('/api/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: activeController.signal,
    body: JSON.stringify(payload)
  });

  if (seq !== searchSeq) return { stale: true };
  if (!res.ok) return { stale: false, items: [], storeStatuses: {} };

  const data = await res.json();
  return {
    stale: seq !== searchSeq,
    items: (data.items || []).map(it => ({ ...it, source: 'live' })),
    storeStatuses: data.storeStatuses || {}
  };
}
