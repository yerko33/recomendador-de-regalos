/**
 * Renderizado de vistas, tarjetas, estado vacío y delegación de eventos.
 */

import {
  $,
  buildStoreUrl,
  escapeHTML,
  formatCLP,
  getStoreById,
  sanitizeBgColor,
  sanitizeHttpUrl
} from './utils.js';

export function renderStoreMarquee(stores) {
  const wrap = $('store-marquee');
  if (!wrap) return;
  wrap.innerHTML = stores
    .map(s => `<span class="store-chip ${escapeHTML(s.cls)}">${escapeHTML(s.name)}</span>`)
    .join('');
}

export function renderInterestChips(interests, selectedInterests) {
  const wrap = $('interests-container');
  wrap.innerHTML = interests.map(i => `
    <button type="button" class="interest-chip ${selectedInterests.includes(i.id) ? 'selected' : ''}" data-interest="${escapeHTML(i.id)}">
      <span>${escapeHTML(i.icon)}</span> ${escapeHTML(i.label)}
    </button>
  `).join('');
}

export function renderStoreFilters(stores, selectedStores) {
  const wrap = $('store-filters');
  wrap.innerHTML = stores.map(s => `
    <button type="button" class="store-toggle ${selectedStores.includes(s.id) ? 'selected' : ''}" data-store-toggle="${escapeHTML(s.id)}">
      ${selectedStores.includes(s.id) ? '✓ ' : ''}${escapeHTML(s.name)}
    </button>
  `).join('');
}

export function createGiftCardHTML(item, rankIndex, { stores, interests, isSaved, showNote = false }) {
  const store = getStoreById(stores, item.storeId);
  const safeUrl = escapeHTML(buildStoreUrl(stores, item));
  const safeTitle = escapeHTML(item.title);
  const safeStoreName = escapeHTML(store.name);
  const safeStoreCls = escapeHTML(store.cls);
  const safeId = escapeHTML(item.id);
  const safeBg = sanitizeBgColor(item.bg);
  const safeEmoji = escapeHTML(item.emoji || '🎁');
  const safeImgUrl = item.image ? sanitizeHttpUrl(item.image) : '';

  let rankBanner = '';
  let rankClass = '';
  if (rankIndex === 0) {
    rankClass = 'rank-1';
    rankBanner = `<div class="rank-banner"><span>🥇 #1 Mejor Recomendación</span><span class="match-badge">${Number(item.matchPct) || 95}% Match</span></div>`;
  } else if (rankIndex === 1) {
    rankClass = 'rank-2';
    rankBanner = `<div class="rank-banner"><span>🥈 #2 Excelente Alternativa</span><span class="match-badge">${Number(item.matchPct) || 91}% Match</span></div>`;
  } else if (rankIndex === 2) {
    rankClass = 'rank-3';
    rankBanner = `<div class="rank-banner"><span>🥉 #3 Top Selección</span><span class="match-badge">${Number(item.matchPct) || 88}% Match</span></div>`;
  }

  const visualContent = safeImgUrl && safeImgUrl !== '#'
    ? `<img src="${escapeHTML(safeImgUrl)}" alt="${safeTitle}" class="gift-visual-img" loading="lazy" referrerpolicy="no-referrer" /><span class="gift-visual-emoji" aria-hidden="true" style="display:none">${safeEmoji}</span>`
    : `<span class="gift-visual-emoji" aria-hidden="true">${safeEmoji}</span>`;

  const interestTags = (item.interests || []).slice(0, 3).map(id => {
    const obj = interests.find(i => i.id === id);
    return obj ? `<span class="gift-tag">${escapeHTML(obj.icon)} ${escapeHTML(obj.label)}</span>` : '';
  }).join('');

  const sourceTag = item.source === 'live'
    ? '<span class="gift-source-tag">⚡ Scraping en vivo</span>'
    : '<span class="gift-source-tag">Catálogo Demo CL</span>';

  const noteSection = showNote
    ? `<div class="saved-note-box">
         <input type="text" class="saved-note-input" value="${escapeHTML(item.note || '')}" placeholder="Agregar nota (ej: Para mamá)..." />
         <button type="button" class="btn-secondary btn-sm" data-save-note="${safeId}">OK</button>
       </div>`
    : '';

  return `
    <article class="gift-card ${rankClass}">
      ${rankBanner}
      <div class="gift-visual" style="background:${safeBg}">
        <span class="gift-store-badge ${safeStoreCls}">${safeStoreName}</span>
        ${visualContent}
        ${sourceTag}
      </div>
      <div class="gift-body">
        <h4 class="gift-title">${safeTitle}</h4>
        <div class="gift-price-row">
          <div>
            <span class="gift-price">${formatCLP(item.price)}</span>
            ${item.oldPrice && item.oldPrice > item.price ? `<span class="gift-old-price">${formatCLP(item.oldPrice)}</span>` : ''}
          </div>
          <span class="gift-rating">★ ${(Number(item.rating) || 4.7).toFixed(1)}</span>
        </div>
        ${item.reasonText ? `<div class="gift-reason">${escapeHTML(item.reasonText)}</div>` : ''}
        ${interestTags ? `<div class="gift-tags">${interestTags}</div>` : ''}
        ${noteSection}
        <div class="gift-actions">
          <a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="btn-primary btn-sm">
            Ver en ${safeStoreName} ↗
          </a>
          <button type="button" class="btn-save ${isSaved ? 'saved' : ''}" data-toggle-save="${safeId}">
            ${isSaved ? '❤️ Guardado' : '🤍 Guardar'}
          </button>
        </div>
      </div>
    </article>
  `;
}

export function attachImageErrorFallbacks(container) {
  container.querySelectorAll('img.gift-visual-img').forEach(img => {
    img.addEventListener('error', () => {
      img.style.display = 'none';
      if (img.nextElementSibling) img.nextElementSibling.style.display = 'inline-block';
    }, { once: true });
  });
}

export function renderResultsSection(state, { stores, interests, isSavedFn }) {
  const section = $('results-section');
  section.classList.remove('hidden');

  const total = state.currentResults.length;
  const liveCount = state.currentResults.filter(x => x.source === 'live').length;
  const liveNote = liveCount > 0 ? ` (${liveCount} obtenidos por scraping en vivo)` : '';

  if (total === 0) {
    $('results-subtitle').textContent = `No encontramos coincidencias entre ${formatCLP(state.budgetMin)} y ${formatCLP(state.budgetMax)}.`;
    $('top3-grid').innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1;">
        <div class="empty-icon">🔎</div>
        <h3>Sin resultados para esta combinación</h3>
        <p>Prueba ampliando el presupuesto, seleccionando más tiendas o usando palabras más generales en tus referencias.</p>
      </div>
    `;
    $('more-section').classList.add('hidden');
    return;
  }

  $('results-subtitle').textContent = `Encontramos ${total} sugerencias entre ${formatCLP(state.budgetMin)} y ${formatCLP(state.budgetMax)}${liveNote}.`;

  const top3 = state.currentResults.slice(0, 3);
  let rest = state.currentResults.slice(3);

  if (state.sortBy === 'price-asc') rest = [...rest].sort((a, b) => a.price - b.price);
  else if (state.sortBy === 'price-desc') rest = [...rest].sort((a, b) => b.price - a.price);
  else if (state.sortBy === 'rating') rest = [...rest].sort((a, b) => (b.rating || 0) - (a.rating || 0));

  $('top3-grid').innerHTML = top3.map((item, idx) =>
    createGiftCardHTML(item, idx, { stores, interests, isSaved: isSavedFn(item.id) })
  ).join('');

  const moreSection = $('more-section');
  if (rest.length === 0) {
    moreSection.classList.add('hidden');
  } else {
    moreSection.classList.remove('hidden');
    const visibleRest = rest.slice(0, state.visibleMoreCount);
    $('more-grid').innerHTML = visibleRest.map(item =>
      createGiftCardHTML(item, -1, { stores, interests, isSaved: isSavedFn(item.id) })
    ).join('');
    $('more-count-label').textContent = `Mostrando ${visibleRest.length} de ${rest.length} sugerencias adicionales en tiendas de Chile`;

    const loadMoreBtn = $('btn-load-more');
    if (visibleRest.length < rest.length) {
      loadMoreBtn.classList.remove('hidden');
      loadMoreBtn.textContent = `Ver más sugerencias (${rest.length - visibleRest.length} restantes)`;
    } else {
      loadMoreBtn.classList.add('hidden');
    }
  }

  attachImageErrorFallbacks(section);
}

export function renderAuthNav(user, { onLogin, onRegister, onLogout }) {
  const area = $('nav-auth-area');
  if (user) {
    const safeName = escapeHTML(user.name || 'Usuario');
    const initials = escapeHTML((user.name || 'U').slice(0, 2).toUpperCase());
    area.innerHTML = `
      <div class="user-pill">
        <span class="user-avatar">${initials}</span>
        <span>${safeName}</span>
      </div>
      <button type="button" class="btn-ghost btn-sm" id="btn-logout">Salir</button>
    `;
    $('btn-logout').addEventListener('click', onLogout);
  } else {
    area.innerHTML = `
      <button type="button" class="btn-ghost btn-sm" id="btn-open-login">Iniciar sesión</button>
      <button type="button" class="btn-primary btn-sm" id="btn-open-register">Crear cuenta</button>
    `;
    $('btn-open-login').addEventListener('click', onLogin);
    $('btn-open-register').addEventListener('click', onRegister);
  }
}

export function renderSavedViewSection(user, { stores, interests, onShareList }) {
  const gate = $('saved-auth-gate');
  const content = $('saved-content');
  const headerActions = $('saved-header-actions');

  if (!user) {
    gate.classList.remove('hidden');
    content.classList.add('hidden');
    headerActions.innerHTML = '';
    return;
  }

  gate.classList.add('hidden');
  content.classList.remove('hidden');
  headerActions.innerHTML = `<button type="button" class="btn-secondary btn-sm" id="btn-share-list">📋 Copiar resumen de mi lista</button>`;
  $('btn-share-list').addEventListener('click', onShareList);

  const gifts = user.savedGifts || [];
  const giftsGrid = $('saved-gifts-grid');
  const giftsEmpty = $('saved-gifts-empty');
  if (gifts.length === 0) {
    giftsGrid.innerHTML = '';
    giftsEmpty.classList.remove('hidden');
  } else {
    giftsEmpty.classList.add('hidden');
    giftsGrid.innerHTML = gifts.map(g =>
      createGiftCardHTML(g, -1, { stores, interests, isSaved: true, showNote: true })
    ).join('');
    attachImageErrorFallbacks(giftsGrid);
  }

  const searches = user.savedSearches || [];
  const searchesList = $('saved-searches-list');
  const searchesEmpty = $('saved-searches-empty');
  if (searches.length === 0) {
    searchesList.innerHTML = '';
    searchesEmpty.classList.remove('hidden');
  } else {
    searchesEmpty.classList.add('hidden');
    searchesList.innerHTML = searches.map(s => {
      const interestNames = (s.interests || [])
        .map(id => interests.find(x => x.id === id)?.label)
        .filter(Boolean)
        .join(', ') || 'Todos los intereses';
      const dateStr = new Date(s.createdAt).toLocaleDateString('es-CL', {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
      });
      return `
        <div class="search-history-item">
          <div>
            <h4>${escapeHTML(interestNames)} · ${formatCLP(s.budgetMin)} – ${formatCLP(s.budgetMax)}</h4>
            <p>${s.reference ? `"${escapeHTML(s.reference)}" · ` : ''}Guardada el ${escapeHTML(dateStr)}${s.topTitle ? ` · Top #1: ${escapeHTML(s.topTitle)}` : ''}</p>
          </div>
          <button type="button" class="btn-secondary btn-sm" data-rerun-search="${escapeHTML(s.id)}">Repetir búsqueda</button>
        </div>
      `;
    }).join('');
  }
}
