/**
 * Punto de entrada principal de RegalaCL (orquestación de estado y eventos).
 */

import { detectBackendAndCreateStore } from './auth-store.js';
import { fetchLiveScrape, loadDomainRules, rankAndDiversify } from './recommender.js';
import {
  renderAuthNav,
  renderInterestChips,
  renderResultsSection,
  renderSavedViewSection,
  renderStoreFilters,
  renderStoreMarquee
} from './ui-render.js';
import { $, escapeHTML, formatCLP, getStoreById, showToast } from './utils.js';

const STORES = window.REGALACL_STORES || [];
const INTERESTS = window.REGALACL_INTERESTS || [];
const CATALOG = window.REGALACL_CATALOG || [];

const state = {
  backendOnline: false,
  storeAdapter: null,
  user: null,
  selectedInterests: [],
  selectedStores: STORES.map(s => s.id),
  budgetMin: 0,
  budgetMax: 20000,
  recipient: 'cualquiera',
  occasion: 'cualquiera',
  reference: '',
  currentResults: [],
  visibleMoreCount: 9,
  sortBy: 'score',
  authMode: 'login',
  pendingAction: null
};

const isSaved = (giftId) => !!(state.user && (state.user.savedGifts || []).some(g => g.id === giftId));

function updateSavedCounters() {
  const gCount = state.user ? (state.user.savedGifts || []).length : 0;
  const sCount = state.user ? (state.user.savedSearches || []).length : 0;
  $('saved-count-pill').textContent = gCount;
  $('tab-gifts-count').textContent = gCount;
  $('tab-searches-count').textContent = sCount;
}

function syncPresetHighlight() {
  $('budget-presets').querySelectorAll('.preset-btn').forEach(b => {
    const match = Number(b.getAttribute('data-min')) === state.budgetMin
      && Number(b.getAttribute('data-max')) === state.budgetMax;
    b.classList.toggle('active', match);
  });
}

function syncFormUI() {
  $('budget-min').value = state.budgetMin;
  $('budget-max').value = state.budgetMax;
  $('recipient-select').value = state.recipient;
  $('occasion-select').value = state.occasion;
  $('reference-input').value = state.reference;
  syncPresetHighlight();
  renderInterestChips(INTERESTS, state.selectedInterests);
  renderStoreFilters(STORES, state.selectedStores);
}

function renderAll() {
  renderAuthNav(state.user, {
    onLogin: () => openAuthModal('login'),
    onRegister: () => openAuthModal('register'),
    onLogout: handleLogout
  });
  updateSavedCounters();
  renderSavedView();
  if (state.currentResults.length) {
    renderResultsSection(state, { stores: STORES, interests: INTERESTS, isSavedFn: isSaved });
  }
}

function renderSavedView() {
  renderSavedViewSection(state.user, {
    stores: STORES,
    interests: INTERESTS,
    onShareList: async () => {
      const gifts = state.user?.savedGifts || [];
      if (!gifts.length) return showToast('Tu lista está vacía.');
      const text = 'Mis ideas de regalos en RegalaCL:\n' + gifts.map((g, i) =>
        `${i + 1}. ${g.title} (${formatCLP(g.price)} en ${getStoreById(STORES, g.storeId).name})${g.note ? ' — ' + g.note : ''}`
      ).join('\n');
      try {
        if (!navigator.clipboard) throw new Error('no-clipboard');
        await navigator.clipboard.writeText(text);
        showToast('¡Lista copiada al portapapeles!');
      } catch {
        showToast('No se pudo copiar automáticamente al portapapeles.');
      }
    }
  });
}

function switchView(viewName) {
  $('view-finder').classList.toggle('hidden', viewName !== 'finder');
  $('view-saved').classList.toggle('hidden', viewName !== 'saved');
  $('btn-view-finder').classList.toggle('active', viewName === 'finder');
  $('btn-view-saved').classList.toggle('active', viewName === 'saved');
  if (viewName === 'saved') renderSavedView();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function openAuthModal(mode = 'login', customDesc = null) {
  state.authMode = mode;
  $('auth-modal').classList.remove('hidden');
  $('auth-error').classList.add('hidden');
  document.querySelectorAll('.modal-tab').forEach(t => {
    t.classList.toggle('active', t.getAttribute('data-auth-mode') === mode);
  });
  const isReg = mode === 'register';
  $('auth-name-group').classList.toggle('hidden', !isReg);
  $('auth-password').minLength = isReg ? 8 : 4;
  $('auth-password').placeholder = isReg ? 'Mínimo 8 caracteres' : 'Tu contraseña';
  $('auth-modal-title').textContent = isReg ? 'Crea tu cuenta en RegalaCL' : 'Bienvenido de vuelta';
  $('auth-modal-desc').textContent = customDesc || (isReg
    ? 'Guarda tus recomendaciones favoritas y ten listas para cada ocasión.'
    : 'Ingresa para acceder a tus regalos guardados.');
  $('auth-submit-btn').textContent = isReg ? 'Crear cuenta gratis' : 'Iniciar sesión';
}

function closeAuthModal() {
  $('auth-modal').classList.add('hidden');
}

function finishLogin(user) {
  state.user = user;
  closeAuthModal();
  renderAll();
  showToast(`¡Hola, ${state.user.name}! Sesión iniciada.`);
  if (typeof state.pendingAction === 'function') {
    const fn = state.pendingAction;
    state.pendingAction = null;
    fn();
  }
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const email = $('auth-email').value.trim().toLowerCase();
  const password = $('auth-password').value;
  const name = $('auth-name').value.trim();
  const errEl = $('auth-error');
  errEl.classList.add('hidden');

  const minLen = state.authMode === 'register' ? 8 : 4;
  if (!email || password.length < minLen) {
    errEl.textContent = state.authMode === 'register'
      ? 'Ingresa un correo válido y una contraseña de al menos 8 caracteres.'
      : 'Ingresa un correo válido y una contraseña de al menos 4 caracteres.';
    errEl.classList.remove('hidden');
    return;
  }

  const res = await state.storeAdapter.auth(state.authMode, { name, email, password });
  if (!res.ok) {
    errEl.textContent = res.error || 'Error de autenticación.';
    errEl.classList.remove('hidden');
    return;
  }
  finishLogin(res.user);
}

async function handleDemoLogin() {
  const res = await state.storeAdapter.loginDemo();
  if (res.ok) finishLogin(res.user);
  else showToast(res.error || 'No se pudo iniciar la cuenta demo.');
}

async function handleLogout() {
  await state.storeAdapter.logout(state.user);
  state.user = null;
  renderAll();
  showToast('Sesión cerrada.');
}

function handleSessionExpired() {
  state.user = null;
  renderAll();
  openAuthModal('login', 'Tu sesión expiró. Vuelve a iniciar sesión para guardar cambios.');
}

async function toggleSaveGift(item) {
  if (!state.user) {
    state.pendingAction = () => toggleSaveGift(item);
    openAuthModal('register', 'Crea una cuenta o inicia sesión para guardar este regalo.');
    return;
  }
  const idx = (state.user.savedGifts || []).findIndex(g => g.id === item.id);
  if (idx >= 0) {
    const [removed] = state.user.savedGifts.splice(idx, 1);
    const res = await state.storeAdapter.removeGift(state.user, item.id);
    if (!res.ok) {
      state.user.savedGifts.splice(idx, 0, removed);
      return res.expired ? handleSessionExpired() : showToast('No se pudo eliminar el regalo.');
    }
    showToast('Regalo eliminado de tus guardados.');
  } else {
    const savedItem = { ...item, savedAt: new Date().toISOString(), note: '' };
    state.user.savedGifts = [savedItem, ...(state.user.savedGifts || [])];
    const res = await state.storeAdapter.saveGift(state.user, savedItem);
    if (!res.ok) {
      state.user.savedGifts.shift();
      return res.expired ? handleSessionExpired() : showToast('No se pudo guardar el regalo.');
    }
    showToast('¡Regalo guardado en tu cuenta!');
  }
  renderAll();
}

async function updateGiftNote(giftId, note) {
  if (!state.user) return;
  const item = (state.user.savedGifts || []).find(g => g.id === giftId);
  if (!item) return;
  const prevNote = item.note;
  item.note = note;
  const res = await state.storeAdapter.updateNote(state.user, giftId, note);
  if (!res.ok) {
    item.note = prevNote;
    return res.expired ? handleSessionExpired() : showToast('No se pudo guardar la nota.');
  }
  showToast('Nota guardada.');
}

async function saveCurrentSearch() {
  if (!state.user) {
    state.pendingAction = () => saveCurrentSearch();
    openAuthModal('register', 'Crea tu cuenta para guardar esta búsqueda en tu historial.');
    return;
  }
  const entry = {
    id: 'srch-' + Date.now(),
    createdAt: new Date().toISOString(),
    interests: [...state.selectedInterests],
    budgetMin: state.budgetMin,
    budgetMax: state.budgetMax,
    recipient: state.recipient,
    occasion: state.occasion,
    reference: state.reference,
    stores: [...state.selectedStores],
    topTitle: state.currentResults[0]?.title || ''
  };
  state.user.savedSearches = [entry, ...(state.user.savedSearches || [])].slice(0, 20);
  const res = await state.storeAdapter.saveSearch(state.user, entry);
  if (!res.ok) {
    state.user.savedSearches.shift();
    return res.expired ? handleSessionExpired() : showToast('No se pudo guardar la búsqueda.');
  }
  updateSavedCounters();
  renderSavedView();
  showToast('Búsqueda guardada en tu historial.');
}

async function runRecommendationSearch(e) {
  if (e) e.preventDefault();

  const rawMin = parseInt($('budget-min').value, 10);
  const rawMax = parseInt($('budget-max').value, 10);
  state.budgetMin = Number.isNaN(rawMin) ? 0 : Math.max(0, rawMin);
  state.budgetMax = Number.isNaN(rawMax) ? 20000 : Math.max(5000, rawMax);
  state.recipient = $('recipient-select').value;
  state.occasion = $('occasion-select').value;
  state.reference = $('reference-input').value.trim();

  if (state.budgetMin > state.budgetMax) {
    [state.budgetMin, state.budgetMax] = [state.budgetMax, state.budgetMin];
  }
  $('budget-min').value = state.budgetMin;
  $('budget-max').value = state.budgetMax;
  syncPresetHighlight();

  const activeStores = state.selectedStores.length ? state.selectedStores : STORES.map(s => s.id);
  const loader = $('scraping-loader');
  const progressWrap = $('scraping-stores-progress');
  $('results-section').classList.add('hidden');
  loader.classList.remove('hidden');
  progressWrap.innerHTML = activeStores.map(sid => {
    const st = getStoreById(STORES, sid);
    return `<span class="scrape-pill" data-store="${escapeHTML(sid)}">⏳ ${escapeHTML(st.name)}</span>`;
  }).join('');

  let liveItems = [];
  let storeStatuses = {};

  if (state.backendOnline) {
    try {
      const resp = await fetchLiveScrape({
        interests: state.selectedInterests,
        reference: state.reference,
        budgetMin: state.budgetMin,
        budgetMax: state.budgetMax,
        stores: activeStores,
        recipient: state.recipient
      });
      if (resp.stale) return;
      liveItems = resp.items;
      storeStatuses = resp.storeStatuses;
    } catch (err) {
      if (err?.name === 'AbortError') return;
    }
  }

  for (const sid of activeStores) {
    const pill = progressWrap.querySelector(`[data-store="${CSS.escape(sid)}"]`);
    if (pill) {
      const st = getStoreById(STORES, sid);
      const info = storeStatuses[sid];
      pill.textContent = info?.liveCount > 0 ? `✓ ${st.name} (${info.liveCount} en vivo)` : `✓ ${st.name}`;
      pill.classList.add('done');
    }
    await new Promise(r => setTimeout(r, state.backendOnline ? 30 : 60));
  }

  const query = {
    interests: state.selectedInterests,
    budgetMin: state.budgetMin,
    budgetMax: state.budgetMax,
    recipient: state.recipient,
    occasion: state.occasion,
    reference: state.reference,
    stores: activeStores
  };

  const liveWithImages = liveItems.filter(it => it.image);
  const rawPool = liveWithImages.length >= 3
    ? liveWithImages
    : [...liveWithImages, ...CATALOG.map(c => ({ ...c, source: 'demo' }))];

  state.currentResults = rankAndDiversify(rawPool, query, INTERESTS);
  state.visibleMoreCount = 9;

  loader.classList.add('hidden');
  renderResultsSection(state, { stores: STORES, interests: INTERESTS, isSavedFn: isSaved });
  $('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function bindDelegatedEvents() {
  // Intereses y tiendas (delegación única sin re-adjuntar listeners en cada clic)
  $('interests-container').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-interest]');
    if (!btn) return;
    const id = btn.getAttribute('data-interest');
    state.selectedInterests = state.selectedInterests.includes(id)
      ? state.selectedInterests.filter(x => x !== id)
      : [...state.selectedInterests, id];
    renderInterestChips(INTERESTS, state.selectedInterests);
  });

  $('store-filters').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-store-toggle]');
    if (!btn) return;
    const id = btn.getAttribute('data-store-toggle');
    if (state.selectedStores.includes(id)) {
      if (state.selectedStores.length > 1) {
        state.selectedStores = state.selectedStores.filter(x => x !== id);
      }
    } else {
      state.selectedStores = [...state.selectedStores, id];
    }
    renderStoreFilters(STORES, state.selectedStores);
  });

  // Acciones en tarjetas (resultados y guardados)
  $('results-section').addEventListener('click', (e) => {
    const saveBtn = e.target.closest('[data-toggle-save]');
    if (!saveBtn) return;
    const item = state.currentResults.find(x => x.id === saveBtn.getAttribute('data-toggle-save'));
    if (item) toggleSaveGift(item);
  });

  $('saved-content').addEventListener('click', (e) => {
    const saveBtn = e.target.closest('[data-toggle-save]');
    if (saveBtn) {
      const item = (state.user?.savedGifts || []).find(x => x.id === saveBtn.getAttribute('data-toggle-save'));
      if (item) toggleSaveGift(item);
      return;
    }
    const noteBtn = e.target.closest('[data-save-note]');
    if (noteBtn) {
      const id = noteBtn.getAttribute('data-save-note');
      const input = noteBtn.closest('.saved-note-box')?.querySelector('.saved-note-input');
      if (input) updateGiftNote(id, input.value.trim());
      return;
    }
    const rerunBtn = e.target.closest('[data-rerun-search]');
    if (rerunBtn) {
      const s = (state.user?.savedSearches || []).find(x => x.id === rerunBtn.getAttribute('data-rerun-search'));
      if (!s) return;
      state.selectedInterests = [...(s.interests || [])];
      state.budgetMin = s.budgetMin;
      state.budgetMax = s.budgetMax;
      state.recipient = s.recipient || 'cualquiera';
      state.occasion = s.occasion || 'cualquiera';
      state.reference = s.reference || '';
      state.selectedStores = [...(s.stores || STORES.map(st => st.id))];
      syncFormUI();
      switchView('finder');
      runRecommendationSearch();
    }
  });
}

function bindGlobalEvents() {
  $('btn-view-finder').addEventListener('click', () => switchView('finder'));
  $('btn-view-saved').addEventListener('click', () => switchView('saved'));
  $('nav-home').addEventListener('click', (e) => { e.preventDefault(); switchView('finder'); });
  $('btn-go-find').addEventListener('click', () => switchView('finder'));

  $('budget-presets').addEventListener('click', (e) => {
    const btn = e.target.closest('.preset-btn');
    if (!btn) return;
    state.budgetMin = parseInt(btn.getAttribute('data-min'), 10);
    state.budgetMax = parseInt(btn.getAttribute('data-max'), 10);
    $('budget-min').value = state.budgetMin;
    $('budget-max').value = state.budgetMax;
    syncPresetHighlight();
  });

  ['budget-min', 'budget-max'].forEach(id => {
    $(id).addEventListener('input', () => {
      state.budgetMin = parseInt($('budget-min').value, 10) || 0;
      state.budgetMax = parseInt($('budget-max').value, 10) || 0;
      syncPresetHighlight();
    });
  });

  $('gift-finder-form').addEventListener('submit', runRecommendationSearch);
  $('btn-reset').addEventListener('click', () => {
    state.selectedInterests = [];
    state.selectedStores = STORES.map(s => s.id);
    state.budgetMin = 0;
    state.budgetMax = 20000;
    state.recipient = 'cualquiera';
    state.occasion = 'cualquiera';
    state.reference = '';
    syncFormUI();
    $('results-section').classList.add('hidden');
  });

  $('sort-select').addEventListener('change', (e) => {
    state.sortBy = e.target.value;
    renderResultsSection(state, { stores: STORES, interests: INTERESTS, isSavedFn: isSaved });
  });

  $('btn-load-more').addEventListener('click', () => {
    state.visibleMoreCount += 12;
    renderResultsSection(state, { stores: STORES, interests: INTERESTS, isSavedFn: isSaved });
  });

  $('btn-save-search').addEventListener('click', saveCurrentSearch);

  document.querySelectorAll('.saved-tabs .tab-btn').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.saved-tabs .tab-btn').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const which = tab.getAttribute('data-tab');
      $('saved-gifts-panel').classList.toggle('hidden', which !== 'gifts');
      $('saved-searches-panel').classList.toggle('hidden', which !== 'searches');
    });
  });

  $('btn-gate-register').addEventListener('click', () => openAuthModal('register'));
  $('btn-gate-login').addEventListener('click', () => openAuthModal('login'));
  $('btn-gate-demo').addEventListener('click', handleDemoLogin);

  $('auth-modal-close').addEventListener('click', closeAuthModal);
  $('auth-modal').addEventListener('click', (e) => { if (e.target === $('auth-modal')) closeAuthModal(); });
  document.querySelectorAll('.modal-tab').forEach(tab => {
    tab.addEventListener('click', () => openAuthModal(tab.getAttribute('data-auth-mode')));
  });
  $('auth-form').addEventListener('submit', handleAuthSubmit);
  $('auth-demo-btn').addEventListener('click', handleDemoLogin);
}

async function init() {
  renderStoreMarquee(STORES);
  renderInterestChips(INTERESTS, state.selectedInterests);
  renderStoreFilters(STORES, state.selectedStores);
  bindDelegatedEvents();
  bindGlobalEvents();

  const [, backend] = await Promise.all([
    loadDomainRules(),
    detectBackendAndCreateStore(CATALOG)
  ]);

  state.backendOnline = backend.backendOnline;
  state.storeAdapter = backend.store;

  if (backend.backendOnline) {
    $('backend-status-dot').classList.add('live');
    if (backend.mode === 'vercel_serverless') {
      $('backend-status-title').textContent = 'Scraper en Vivo Activo (Cloud)';
      $('backend-status-desc').textContent = 'Búsqueda en tiempo real en Falabella, Ripley, Paris, Abcdin / La Polar, Jumbo y Santa Isabel. Cuentas y favoritos guardados en tu navegador.';
    } else {
      $('backend-status-title').textContent = 'Backend Scraper + SQLite Activo';
      $('backend-status-desc').textContent = 'Conectado al servidor local Python. Búsqueda en vivo en tiendas chilenas y cuentas persistidas en SQLite.';
    }
  }

  state.user = await state.storeAdapter.restoreSession();
  renderAll();
}

init();
