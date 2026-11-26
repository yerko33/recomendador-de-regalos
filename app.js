(function () {
  'use strict';

  const STORES = window.REGALACL_STORES || [];
  const INTERESTS = window.REGALACL_INTERESTS || [];
  const CATALOG = window.REGALACL_CATALOG || [];

  const STORAGE_KEYS = {
    USERS: 'regalacl_users_v1',
    SESSION: 'regalacl_session_v1'
  };

  // App state
  const state = {
    backendOnline: false,
    backendAuth: false,
    user: null,
    selectedInterests: [],
    selectedStores: STORES.map(s => s.id),
    budgetMin: 0,
    budgetMax: 20000,
    recipient: 'cualquiera',
    occasion: 'cualquiera',
    reference: '',
    currentResults: [],
    visibleMoreCount: 6,
    sortBy: 'score',
    authMode: 'login',
    pendingAction: null
  };

  const $ = (id) => document.getElementById(id);

  function formatCLP(amount) {
    return '$' + Math.round(amount).toLocaleString('es-CL');
  }

  function normalizeText(str) {
    return (str || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function getStoreById(storeId) {
    return STORES.find(s => s.id === storeId) || {
      id: storeId,
      name: storeId,
      cls: 'store-mercadolibre',
      searchBase: 'https://www.google.cl/search?q='
    };
  }

  function buildStoreUrl(item) {
    if (item.url) return item.url;
    const store = getStoreById(item.storeId);
    const q = encodeURIComponent(item.title);
    if (store.id === 'mercadolibre' || store.id === 'ripley') {
      return store.searchBase + encodeURIComponent(item.title.replace(/\s+/g, '-'));
    }
    return store.searchBase + q;
  }

  let toastTimer = null;
  function showToast(msg) {
    const el = $('toast');
    el.textContent = msg;
    el.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add('hidden'), 3200);
  }

  async function checkBackend() {
    try {
      const res = await fetch('/api/health', { signal: AbortSignal.timeout(2500) });
      if (res.ok) {
        const info = await res.json().catch(() => ({}));
        state.backendOnline = true;
        state.backendAuth = info.auth !== 'local';
        $('backend-status-dot').classList.add('live');
        if (info.mode === 'vercel_serverless') {
          $('backend-status-title').textContent = 'Scraper en Vivo Activo (Cloud)';
          $('backend-status-desc').textContent = 'Búsqueda en tiempo real en Falabella, Ripley, Paris, Jumbo y Santa Isabel. Cuentas y favoritos guardados en tu navegador.';
        } else {
          $('backend-status-title').textContent = 'Backend Scraper + SQLite Activo';
          $('backend-status-desc').textContent = 'Conectado al servidor local Python. Búsqueda en vivo en tiendas abiertas y cuentas persistidas en SQLite.';
        }
      }
    } catch {
      state.backendOnline = false;
      state.backendAuth = false;
    }
  }

  function loadLocalUsers() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEYS.USERS) || '{}');
    } catch {
      return {};
    }
  }

  function saveLocalUsers(users) {
    localStorage.setItem(STORAGE_KEYS.USERS, JSON.stringify(users));
  }

  function ensureDemoUserLocal() {
    const users = loadLocalUsers();
    if (!users['demo@regalacl.cl']) {
      users['demo@regalacl.cl'] = {
        name: 'Usuario Demo',
        email: 'demo@regalacl.cl',
        password: 'demo',
        savedGifts: [
          {
            ...CATALOG[9],
            savedAt: new Date().toISOString(),
            note: 'Idea para cumpleaños de papá'
          }
        ],
        savedSearches: []
      };
      saveLocalUsers(users);
    }
  }

  function persistCurrentUserLocal() {
    if (!state.user) return;
    const users = loadLocalUsers();
    users[state.user.email] = {
      ...(users[state.user.email] || {}),
      name: state.user.name,
      email: state.user.email,
      savedGifts: state.user.savedGifts || [],
      savedSearches: state.user.savedSearches || []
    };
    saveLocalUsers(users);
    localStorage.setItem(STORAGE_KEYS.SESSION, JSON.stringify({
      email: state.user.email,
      token: state.user.token || null
    }));
  }

  async function restoreSession() {
    ensureDemoUserLocal();
    const raw = localStorage.getItem(STORAGE_KEYS.SESSION);
    if (!raw) return;
    try {
      const session = JSON.parse(raw);
      if (state.backendAuth && session.token) {
        const res = await fetch('/api/me', {
          headers: { Authorization: 'Bearer ' + session.token }
        });
        if (res.ok) {
          const data = await res.json();
          state.user = { ...data.user, token: session.token };
          renderAuthNav();
          updateSavedCounters();
          return;
        }
      }
      const users = loadLocalUsers();
      if (session.email && users[session.email]) {
        state.user = { ...users[session.email] };
        renderAuthNav();
        updateSavedCounters();
      }
    } catch {
      // ignore invalid session
    }
  }

  async function handleAuthSubmit(e) {
    e.preventDefault();
    const email = $('auth-email').value.trim().toLowerCase();
    const password = $('auth-password').value;
    const name = $('auth-name').value.trim();
    const errEl = $('auth-error');
    errEl.classList.add('hidden');

    if (!email || password.length < 4) {
      errEl.textContent = 'Ingresa un correo válido y una contraseña de al menos 4 caracteres.';
      errEl.classList.remove('hidden');
      return;
    }

    if (state.backendAuth) {
      const endpoint = state.authMode === 'register' ? '/api/register' : '/api/login';
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name || email.split('@')[0], email, password })
        });
        const data = await res.json();
        if (!res.ok) {
          errEl.textContent = data.error || 'Error de autenticación.';
          errEl.classList.remove('hidden');
          return;
        }
        state.user = { ...data.user, token: data.token };
        localStorage.setItem(STORAGE_KEYS.SESSION, JSON.stringify({ email: state.user.email, token: data.token }));
        finishLogin();
        return;
      } catch {
        // fallback to local
      }
    }

    const users = loadLocalUsers();
    if (state.authMode === 'register') {
      if (users[email]) {
        errEl.textContent = 'Ya existe una cuenta registrada con este correo.';
        errEl.classList.remove('hidden');
        return;
      }
      users[email] = {
        name: name || email.split('@')[0],
        email,
        password,
        savedGifts: [],
        savedSearches: []
      };
      saveLocalUsers(users);
      state.user = { ...users[email] };
    } else {
      const existing = users[email];
      if (!existing || existing.password !== password) {
        errEl.textContent = 'Correo o contraseña incorrectos.';
        errEl.classList.remove('hidden');
        return;
      }
      state.user = { ...existing };
    }
    persistCurrentUserLocal();
    finishLogin();
  }

  async function loginWithDemo() {
    if (state.backendAuth) {
      try {
        const res = await fetch('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: 'demo@regalacl.cl', password: 'demo' })
        });
        if (res.ok) {
          const data = await res.json();
          state.user = { ...data.user, token: data.token };
          localStorage.setItem(STORAGE_KEYS.SESSION, JSON.stringify({ email: state.user.email, token: data.token }));
          finishLogin();
          return;
        }
      } catch {
        // fallback
      }
    }
    ensureDemoUserLocal();
    const users = loadLocalUsers();
    state.user = { ...users['demo@regalacl.cl'] };
    persistCurrentUserLocal();
    finishLogin();
  }

  function finishLogin() {
    closeAuthModal();
    renderAuthNav();
    updateSavedCounters();
    renderSavedView();
    if (state.currentResults.length) renderResults();
    showToast(`¡Hola, ${state.user.name}! Sesión iniciada.`);

    if (typeof state.pendingAction === 'function') {
      const fn = state.pendingAction;
      state.pendingAction = null;
      fn();
    }
  }

  function logout() {
    state.user = null;
    localStorage.removeItem(STORAGE_KEYS.SESSION);
    renderAuthNav();
    updateSavedCounters();
    renderSavedView();
    if (state.currentResults.length) renderResults();
    showToast('Sesión cerrada.');
  }

  async function toggleSaveGift(item) {
    if (!state.user) {
      state.pendingAction = () => toggleSaveGift(item);
      openAuthModal('register', 'Crea una cuenta o inicia sesión para guardar este regalo.');
      return;
    }

    const existsIdx = (state.user.savedGifts || []).findIndex(g => g.id === item.id);
    if (existsIdx >= 0) {
      state.user.savedGifts.splice(existsIdx, 1);
      if (state.backendAuth && state.user.token) {
        fetch('/api/saved-gifts/' + encodeURIComponent(item.id), {
          method: 'DELETE',
          headers: { Authorization: 'Bearer ' + state.user.token }
        }).catch(() => {});
      }
      showToast('Regalo eliminado de tus guardados.');
    } else {
      const savedItem = { ...item, savedAt: new Date().toISOString(), note: '' };
      state.user.savedGifts = [savedItem, ...(state.user.savedGifts || [])];
      if (state.backendAuth && state.user.token) {
        fetch('/api/saved-gifts', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: 'Bearer ' + state.user.token
          },
          body: JSON.stringify({ gift: savedItem })
        }).catch(() => {});
      }
      showToast('¡Regalo guardado en tu cuenta!');
    }

    persistCurrentUserLocal();
    updateSavedCounters();
    if (state.currentResults.length) renderResults();
    renderSavedView();
  }

  async function updateGiftNote(giftId, note) {
    if (!state.user) return;
    const item = (state.user.savedGifts || []).find(g => g.id === giftId);
    if (!item) return;
    item.note = note;
    persistCurrentUserLocal();
    if (state.backendAuth && state.user.token) {
      fetch('/api/saved-gifts/' + encodeURIComponent(giftId) + '/note', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + state.user.token
        },
        body: JSON.stringify({ note })
      }).catch(() => {});
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
      topTitle: state.currentResults[0] ? state.currentResults[0].title : ''
    };
    state.user.savedSearches = [entry, ...(state.user.savedSearches || [])].slice(0, 20);
    persistCurrentUserLocal();

    if (state.backendAuth && state.user.token) {
      fetch('/api/saved-searches', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + state.user.token
        },
        body: JSON.stringify({ search: entry })
      }).catch(() => {});
    }

    updateSavedCounters();
    renderSavedView();
    showToast('Búsqueda guardada en tu historial.');
  }

  const SPANISH_STOPWORDS = new Set([
    'a', 'al', 'algo', 'ama', 'amiga', 'amigo', 'aniversario', 'bonito', 'bueno',
    'busca', 'buscando', 'busco', 'como', 'con', 'cosas', 'cumpleanos', 'de', 'del',
    'dia', 'el', 'ella', 'en', 'encanta', 'es', 'esa', 'ese', 'especial', 'esta',
    'gusta', 'gustan', 'hacer', 'idea', 'ideas', 'la', 'las', 'le', 'les', 'lindo',
    'lo', 'los', 'mama', 'mas', 'me', 'mi', 'mucho', 'muy', 'navidad', 'papa',
    'para', 'pareja', 'persona', 'por', 'practico', 'que', 'quiere', 'regalar',
    'regalo', 'regalos', 'se', 'si', 'sin', 'sobre', 'solo', 'su', 'sus', 'tiene',
    'todo', 'un', 'una', 'unos', 'usar', 'ver', 'y'
  ]);

  const REF_SYNONYMS = {
    maquillarse: ['maquill', 'labial', 'sombra', 'brocha', 'rubor', 'makeup', 'cosmetic'],
    maquillar: ['maquill', 'labial', 'sombra', 'brocha', 'rubor', 'makeup'],
    dulces: ['reposter', 'pasteler', 'molde', 'hornear', 'galleta', 'cupcake', 'muffin', 'torta', 'queque', 'batidor', 'manga', 'boquilla', 'postre'],
    dulce: ['reposter', 'pasteler', 'molde', 'hornear', 'galleta', 'cupcake', 'muffin', 'torta', 'queque', 'batidor', 'postre'],
    postres: ['reposter', 'pasteler', 'molde', 'hornear', 'galleta', 'cupcake', 'torta', 'batidor', 'postre'],
    galletas: ['galleta', 'cortador', 'molde', 'hornear', 'reposter'],
    cocinar: ['cocin', 'freidor', 'sarten', 'olla', 'cuchill'],
    correr: ['running', 'deport', 'zapatilla'],
    entrenar: ['fitness', 'mancuerna', 'yoga', 'deport'],
    leer: ['libro', 'kindle', 'novela'],
    relajarse: ['spa', 'masaje', 'difusor', 'vela', 'aroma'],
    parrilladas: ['parrill', 'asado', 'cuchillo', 'pinza', 'tenedor', 'tabla'],
    parrillada: ['parrill', 'asado', 'cuchillo', 'pinza', 'tenedor', 'tabla'],
    asados: ['parrill', 'asado'],
    asado: ['parrill', 'asado']
  };

  const CHILD_RECIPIENTS = new Set(['bebe_0_2', 'nino_3_4', 'nino', 'adolescente']);
  const KIDS_TITLE_RE = /\b(infantil\w*|nina\w*|nino\w*|juguete\w*|sorpresa\w*|expendedora|barbie|princesa\w*|frozen|minnie|unicornio|muneca\w*|playset|kids|giotto|tempera|plastilina|sonajero\w*|hello kitty|disney|stitch|maleta make)\b/;
  const NON_GIFTABLE_FOOD_RE = /\b(longaniza\w*|chorizo\w*|choricillo\w*|prieta\w*|salchicha\w*|vienesa\w*|hamburguesa\w*|tocino|carne|vacuno|cerdo|pollo|costillar|lomo|entrana|sobrecostilla|huachalomo|abastero|plateada|chuleta\w*|cebolla\w*|cereal\w*|arroz|fideo\w*|tallarin\w*|harina|azucar|leche|yogurt|queso|jamon|aceite|salsa|ketchup|mayonesa|mostaza|pebre|chimichurri|carbon|briqueta\w*|detergente|cloro|congelado\w*)\b/;

  function scoreProduct(item, query) {
    let score = 30;
    const reasons = [];

    if (query.stores.length && !query.stores.includes(item.storeId)) {
      return null;
    }

    const tNorm = normalizeText(item.title);
    // Hard filter: exclude non-giftable groceries/meat
    if (NON_GIFTABLE_FOOD_RE.test(tNorm)) {
      return null;
    }
    // Hard filter: exclude kids toys/sets when recipient is adult and user didn't pick juguetes/bebes
    const isAdult = query.recipient !== 'cualquiera' && !CHILD_RECIPIENTS.has(query.recipient);
    const wantsKids = query.interests.includes('juguetes') || query.interests.includes('bebes');
    if (isAdult && !wantsKids && KIDS_TITLE_RE.test(tNorm)) {
      return null;
    }

    const { budgetMin, budgetMax } = query;
    if (item.price >= budgetMin && item.price <= budgetMax) {
      score += 30;
      reasons.push('dentro de tu presupuesto');
    } else if (item.price < budgetMin && item.price >= budgetMin * 0.7) {
      score += 12;
      reasons.push('opción más económica');
    } else if (item.price > budgetMax && item.price <= budgetMax * 1.2) {
      score += 6;
      reasons.push('ligeramente sobre el tope');
    } else {
      score -= 30;
    }

    // Interest match
    const matchedInterests = (item.interests || []).filter(i => query.interests.includes(i));
    if (matchedInterests.length > 0) {
      score += Math.min(28, matchedInterests.length * 16);
      const labels = matchedInterests
        .map(id => INTERESTS.find(x => x.id === id)?.label)
        .filter(Boolean);
      reasons.push('coincide con ' + labels.slice(0, 2).join(' y '));
    }

    // Free-text reference match (clean Spanish stopwords + stem/synonym matching on actual title)
    const refNorm = normalizeText(query.reference);
    if (refNorm) {
      const tokens = refNorm.split(' ').filter(t => t.length >= 3 && !SPANISH_STOPWORDS.has(t));
      const haystack = normalizeText(item.title);
      let kwHits = 0;
      tokens.forEach(tok => {
        const synonyms = REF_SYNONYMS[tok] || [tok.length >= 6 ? tok.slice(0, 6) : tok];
        if (synonyms.some(s => haystack.includes(s))) {
          kwHits++;
        }
      });
      if (kwHits > 0) {
        score += Math.min(34, kwHits * 18);
        reasons.push('calza directo con tu referencia');
      } else if (tokens.length > 0 && !(item.matchedTerms && item.matchedTerms.length)) {
        // Penalize items that completely miss the user's explicit text reference
        score -= 20;
      }
    }

    if (query.recipient !== 'cualquiera' && (item.recipients || []).includes(query.recipient)) {
      score += 6;
    }
    if (query.occasion !== 'cualquiera' && (item.occasions || []).includes(query.occasion)) {
      score += 6;
    }

    score += Math.round(((item.rating || 4.5) - 4.0) * 5);
    if (item.source === 'live') {
      score += 6;
    }

    const matchPct = Math.max(55, Math.min(99, Math.round(score)));
    const reasonText = reasons.length
      ? 'Ideal porque está ' + reasons.slice(0, 2).join(' y ') + '.'
      : 'Muy bien evaluado y popular como regalo en Chile.';

    return {
      ...item,
      score,
      matchPct,
      reasonText
    };
  }

  async function runRecommendationSearch(e) {
    if (e) e.preventDefault();

    state.budgetMin = Math.max(0, parseInt($('budget-min').value, 10) || 0);
    state.budgetMax = Math.max(5000, parseInt($('budget-max').value, 10) || 500000);
    state.recipient = $('recipient-select').value;
    state.occasion = $('occasion-select').value;
    state.reference = $('reference-input').value.trim();

    if (state.budgetMin > state.budgetMax) {
      const tmp = state.budgetMin;
      state.budgetMin = state.budgetMax;
      state.budgetMax = tmp;
      $('budget-min').value = state.budgetMin;
      $('budget-max').value = state.budgetMax;
    }

    const activeStores = state.selectedStores.length ? state.selectedStores : STORES.map(s => s.id);

    const loader = $('scraping-loader');
    const progressWrap = $('scraping-stores-progress');
    $('results-section').classList.add('hidden');
    loader.classList.remove('hidden');
    progressWrap.innerHTML = activeStores.map(sid => {
      const st = getStoreById(sid);
      return `<span class="scrape-pill" data-store="${sid}">⏳ ${st.name}</span>`;
    }).join('');

    let liveItems = [];
    let storeStatuses = {};

    if (state.backendOnline) {
      try {
        const res = await fetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interests: state.selectedInterests,
            reference: state.reference,
            budgetMin: state.budgetMin,
            budgetMax: state.budgetMax,
            stores: activeStores,
            recipient: state.recipient
          })
        });
        if (res.ok) {
          const data = await res.json();
          liveItems = (data.items || []).map(it => ({ ...it, source: 'live' }));
          storeStatuses = data.storeStatuses || {};
        }
      } catch {
        // graceful fallback
      }
    }

    for (let i = 0; i < activeStores.length; i++) {
      const sid = activeStores[i];
      const pill = progressWrap.querySelector(`[data-store="${sid}"]`);
      if (pill) {
        const st = getStoreById(sid);
        const statusInfo = storeStatuses[sid];
        const label = statusInfo && statusInfo.liveCount > 0
          ? `✓ ${st.name} (${statusInfo.liveCount} en vivo)`
          : `✓ ${st.name}`;
        pill.textContent = label;
        pill.classList.add('done');
      }
      await new Promise(r => setTimeout(r, state.backendOnline ? 40 : 75));
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

    // When live scraped products with real store images are available, prioritize them exclusively
    // so every card shows the exact photo from the corresponding Chilean store.
    const liveWithImages = liveItems.filter(it => it.image);
    const rawCombined = liveWithImages.length >= 3
      ? liveWithImages
      : [...liveWithImages, ...CATALOG.map(c => ({ ...c, source: 'demo' }))];
    // Deduplicar productos idénticos de la misma tienda (ej. múltiples vendedores en Falabella)
    const seenKeys = new Set();
    const combined = rawCombined.filter(item => {
      const k = item.storeId + '::' + normalizeText(item.title);
      if (seenKeys.has(k)) return false;
      seenKeys.add(k);
      return true;
    });
    const scored = combined
      .map(item => scoreProduct(item, query))
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);

    // Mantener solo resultados coherentes (evita que al expandir "Ver más" aparezcan rellenos irrelevantes)
    const minScoreThreshold = scored.length ? Math.max(48, scored[0].score - 28) : 48;
    const coherent = scored.filter((item, idx) => idx < 3 || item.score >= minScoreThreshold);

    // Promote store diversity in Top 3 ONLY among high-scoring candidates (within 12 pts of #1)
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

    state.currentResults = [...top3, ...remaining];
    state.visibleMoreCount = 9;

    loader.classList.add('hidden');
    renderResults(liveItems.length);
    $('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function isSaved(giftId) {
    return !!(state.user && (state.user.savedGifts || []).some(g => g.id === giftId));
  }

  function createGiftCardHTML(item, rankIndex, options = {}) {
    const store = getStoreById(item.storeId);
    const saved = isSaved(item.id);
    const url = buildStoreUrl(item);

    let rankBanner = '';
    let rankClass = '';
    if (rankIndex === 0) {
      rankClass = 'rank-1';
      rankBanner = `<div class="rank-banner"><span>🥇 #1 Mejor Recomendación</span><span class="match-badge">${item.matchPct || 95}% Match</span></div>`;
    } else if (rankIndex === 1) {
      rankClass = 'rank-2';
      rankBanner = `<div class="rank-banner"><span>🥈 #2 Excelente Alternativa</span><span class="match-badge">${item.matchPct || 91}% Match</span></div>`;
    } else if (rankIndex === 2) {
      rankClass = 'rank-3';
      rankBanner = `<div class="rank-banner"><span>🥉 #3 Top Selección</span><span class="match-badge">${item.matchPct || 88}% Match</span></div>`;
    }

    const visualContent = item.image
      ? `<img src="${item.image}" alt="${item.title.replace(/"/g, '&quot;')}" class="gift-visual-img" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block';" /><span class="gift-visual-emoji" aria-hidden="true" style="display:none">${item.emoji || '🎁'}</span>`
      : `<span class="gift-visual-emoji" aria-hidden="true">${item.emoji || '🎁'}</span>`;

    const interestTags = (item.interests || []).slice(0, 3).map(id => {
      const obj = INTERESTS.find(i => i.id === id);
      return obj ? `<span class="gift-tag">${obj.icon} ${obj.label}</span>` : '';
    }).join('');

    const sourceTag = item.source === 'live'
      ? '<span class="gift-source-tag">⚡ Scraping en vivo</span>'
      : '<span class="gift-source-tag">Catálogo Demo CL</span>';

    const noteSection = options.showNote
      ? `<div class="saved-note-box">
           <input type="text" class="saved-note-input" data-note-id="${item.id}" value="${(item.note || '').replace(/"/g, '&quot;')}" placeholder="Agregar nota (ej: Para mamá)..." />
           <button type="button" class="btn-secondary btn-sm" data-save-note="${item.id}">OK</button>
         </div>`
      : '';

    return `
      <article class="gift-card ${rankClass}">
        ${rankBanner}
        <div class="gift-visual" style="background:${item.bg || '#f3efe9'}">
          <span class="gift-store-badge ${store.cls}">${store.name}</span>
          ${visualContent}
          ${sourceTag}
        </div>
        <div class="gift-body">
          <h4 class="gift-title">${item.title}</h4>
          <div class="gift-price-row">
            <div>
              <span class="gift-price">${formatCLP(item.price)}</span>
              ${item.oldPrice && item.oldPrice > item.price ? `<span class="gift-old-price">${formatCLP(item.oldPrice)}</span>` : ''}
            </div>
            <span class="gift-rating">★ ${(item.rating || 4.7).toFixed(1)}</span>
          </div>
          ${item.reasonText ? `<div class="gift-reason">${item.reasonText}</div>` : ''}
          ${interestTags ? `<div class="gift-tags">${interestTags}</div>` : ''}
          ${noteSection}
          <div class="gift-actions">
            <a href="${url}" target="_blank" rel="noopener noreferrer" class="btn-primary btn-sm">
              Ver en ${store.name} ↗
            </a>
            <button type="button" class="btn-save ${saved ? 'saved' : ''}" data-toggle-save="${item.id}">
              ${saved ? '❤️ Guardado' : '🤍 Guardar'}
            </button>
          </div>
        </div>
      </article>
    `;
  }

  function renderResults(liveCount = 0) {
    const section = $('results-section');
    section.classList.remove('hidden');

    const top3 = state.currentResults.slice(0, 3);
    let rest = state.currentResults.slice(3);

    if (state.sortBy === 'price-asc') {
      rest = [...rest].sort((a, b) => a.price - b.price);
    } else if (state.sortBy === 'price-desc') {
      rest = [...rest].sort((a, b) => b.price - a.price);
    } else if (state.sortBy === 'rating') {
      rest = [...rest].sort((a, b) => (b.rating || 0) - (a.rating || 0));
    }

    const total = state.currentResults.length;
    const liveNote = liveCount > 0 ? ` (${liveCount} obtenidos por scraping en vivo)` : '';
    $('results-subtitle').textContent = `Encontramos ${total} sugerencias entre ${formatCLP(state.budgetMin)} y ${formatCLP(state.budgetMax)}${liveNote}.`;

    $('top3-grid').innerHTML = top3.map((item, idx) => createGiftCardHTML(item, idx)).join('');

    const visibleRest = rest.slice(0, state.visibleMoreCount);
    $('more-grid').innerHTML = visibleRest.map(item => createGiftCardHTML(item, -1)).join('');
    $('more-count-label').textContent = `Mostrando ${visibleRest.length} de ${rest.length} sugerencias adicionales en tiendas de Chile`;

    const loadMoreBtn = $('btn-load-more');
    if (visibleRest.length < rest.length) {
      loadMoreBtn.classList.remove('hidden');
      loadMoreBtn.textContent = `Ver más sugerencias (${rest.length - visibleRest.length} restantes)`;
    } else {
      loadMoreBtn.classList.add('hidden');
    }

    bindCardActions(section, state.currentResults);
  }

  function bindCardActions(container, sourceList) {
    container.querySelectorAll('[data-toggle-save]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-toggle-save');
        const item = sourceList.find(x => x.id === id);
        if (item) toggleSaveGift(item);
      });
    });

    container.querySelectorAll('[data-save-note]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-save-note');
        const input = container.querySelector(`[data-note-id="${id}"]`);
        if (input) updateGiftNote(id, input.value.trim());
      });
    });
  }

  function renderAuthNav() {
    const area = $('nav-auth-area');
    if (state.user) {
      const initials = (state.user.name || 'U').slice(0, 2).toUpperCase();
      area.innerHTML = `
        <div class="user-pill">
          <span class="user-avatar">${initials}</span>
          <span>${state.user.name}</span>
        </div>
        <button type="button" class="btn-ghost btn-sm" id="btn-logout">Salir</button>
      `;
      $('btn-logout').addEventListener('click', logout);
    } else {
      area.innerHTML = `
        <button type="button" class="btn-ghost btn-sm" id="btn-open-login">Iniciar sesión</button>
        <button type="button" class="btn-primary btn-sm" id="btn-open-register">Crear cuenta</button>
      `;
      $('btn-open-login').addEventListener('click', () => openAuthModal('login'));
      $('btn-open-register').addEventListener('click', () => openAuthModal('register'));
    }
  }

  function updateSavedCounters() {
    const giftsCount = state.user ? (state.user.savedGifts || []).length : 0;
    const searchesCount = state.user ? (state.user.savedSearches || []).length : 0;
    $('saved-count-pill').textContent = giftsCount;
    $('tab-gifts-count').textContent = giftsCount;
    $('tab-searches-count').textContent = searchesCount;
  }

  function renderSavedView() {
    const gate = $('saved-auth-gate');
    const content = $('saved-content');
    const headerActions = $('saved-header-actions');

    if (!state.user) {
      gate.classList.remove('hidden');
      content.classList.add('hidden');
      headerActions.innerHTML = '';
      return;
    }

    gate.classList.add('hidden');
    content.classList.remove('hidden');
    headerActions.innerHTML = `<button type="button" class="btn-secondary btn-sm" id="btn-share-list">📋 Copiar resumen de mi lista</button>`;
    $('btn-share-list').addEventListener('click', () => {
      const gifts = state.user.savedGifts || [];
      if (!gifts.length) {
        showToast('Tu lista está vacía.');
        return;
      }
      const text = 'Mis ideas de regalos en RegalaCL:\n' + gifts.map((g, i) =>
        `${i + 1}. ${g.title} (${formatCLP(g.price)} en ${getStoreById(g.storeId).name})${g.note ? ' — ' + g.note : ''}`
      ).join('\n');
      navigator.clipboard?.writeText(text);
      showToast('¡Lista copiada al portapapeles!');
    });

    const gifts = state.user.savedGifts || [];
    const giftsGrid = $('saved-gifts-grid');
    const giftsEmpty = $('saved-gifts-empty');
    if (gifts.length === 0) {
      giftsGrid.innerHTML = '';
      giftsEmpty.classList.remove('hidden');
    } else {
      giftsEmpty.classList.add('hidden');
      giftsGrid.innerHTML = gifts.map(g => createGiftCardHTML(g, -1, { showNote: true })).join('');
      bindCardActions(giftsGrid, gifts);
    }

    const searches = state.user.savedSearches || [];
    const searchesList = $('saved-searches-list');
    const searchesEmpty = $('saved-searches-empty');
    if (searches.length === 0) {
      searchesList.innerHTML = '';
      searchesEmpty.classList.remove('hidden');
    } else {
      searchesEmpty.classList.add('hidden');
      searchesList.innerHTML = searches.map(s => {
        const interestNames = (s.interests || [])
          .map(id => INTERESTS.find(x => x.id === id)?.label)
          .filter(Boolean)
          .join(', ') || 'Todos los intereses';
        const dateStr = new Date(s.createdAt).toLocaleDateString('es-CL', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
        return `
          <div class="search-history-item">
            <div>
              <h4>${interestNames} · ${formatCLP(s.budgetMin)} – ${formatCLP(s.budgetMax)}</h4>
              <p>${s.reference ? `"${s.reference}" · ` : ''}Guardada el ${dateStr}${s.topTitle ? ` · Top #1: ${s.topTitle}` : ''}</p>
            </div>
            <button type="button" class="btn-secondary btn-sm" data-rerun-search="${s.id}">Repetir búsqueda</button>
          </div>
        `;
      }).join('');

      searchesList.querySelectorAll('[data-rerun-search]').forEach(btn => {
        btn.addEventListener('click', () => {
          const s = searches.find(x => x.id === btn.getAttribute('data-rerun-search'));
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
        });
      });
    }
  }

  function renderInterestChips() {
    const wrap = $('interests-container');
    wrap.innerHTML = INTERESTS.map(i => `
      <button type="button" class="interest-chip ${state.selectedInterests.includes(i.id) ? 'selected' : ''}" data-interest="${i.id}">
        <span>${i.icon}</span> ${i.label}
      </button>
    `).join('');

    wrap.querySelectorAll('[data-interest]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-interest');
        if (state.selectedInterests.includes(id)) {
          state.selectedInterests = state.selectedInterests.filter(x => x !== id);
        } else {
          state.selectedInterests.push(id);
        }
        renderInterestChips();
      });
    });
  }

  function renderStoreFilters() {
    const wrap = $('store-filters');
    wrap.innerHTML = STORES.map(s => `
      <button type="button" class="store-toggle ${state.selectedStores.includes(s.id) ? 'selected' : ''}" data-store-toggle="${s.id}">
        ${state.selectedStores.includes(s.id) ? '✓ ' : ''}${s.name}
      </button>
    `).join('');

    wrap.querySelectorAll('[data-store-toggle]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-store-toggle');
        if (state.selectedStores.includes(id)) {
          if (state.selectedStores.length > 1) {
            state.selectedStores = state.selectedStores.filter(x => x !== id);
          }
        } else {
          state.selectedStores.push(id);
        }
        renderStoreFilters();
      });
    });
  }

  function syncFormUI() {
    $('budget-min').value = state.budgetMin;
    $('budget-max').value = state.budgetMax;
    $('recipient-select').value = state.recipient;
    $('occasion-select').value = state.occasion;
    $('reference-input').value = state.reference;
    renderInterestChips();
    renderStoreFilters();
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
    $('auth-modal-title').textContent = isReg ? 'Crea tu cuenta en RegalaCL' : 'Bienvenido de vuelta';
    $('auth-modal-desc').textContent = customDesc || (isReg
      ? 'Guarda tus recomendaciones favoritas y ten listas para cada ocasión.'
      : 'Ingresa para acceder a tus regalos guardados.');
    $('auth-submit-btn').textContent = isReg ? 'Crear cuenta gratis' : 'Iniciar sesión';
  }

  function closeAuthModal() {
    $('auth-modal').classList.add('hidden');
  }

  function bindGlobalEvents() {
    $('btn-view-finder').addEventListener('click', () => switchView('finder'));
    $('btn-view-saved').addEventListener('click', () => switchView('saved'));
    $('nav-home').addEventListener('click', (e) => { e.preventDefault(); switchView('finder'); });
    $('btn-go-find').addEventListener('click', () => switchView('finder'));

    $('budget-presets').querySelectorAll('.preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        $('budget-presets').querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.budgetMin = parseInt(btn.getAttribute('data-min'), 10);
        state.budgetMax = parseInt(btn.getAttribute('data-max'), 10);
        $('budget-min').value = state.budgetMin;
        $('budget-max').value = state.budgetMax;
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
      $('budget-presets').querySelectorAll('.preset-btn').forEach(b => {
        const isDefault = b.getAttribute('data-min') === '0' && b.getAttribute('data-max') === '20000';
        b.classList.toggle('active', isDefault);
      });
      syncFormUI();
      $('results-section').classList.add('hidden');
    });

    $('sort-select').addEventListener('change', (e) => {
      state.sortBy = e.target.value;
      renderResults();
    });

    $('btn-load-more').addEventListener('click', () => {
      state.visibleMoreCount += 12;
      renderResults();
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
    $('btn-gate-demo').addEventListener('click', loginWithDemo);

    $('auth-modal-close').addEventListener('click', closeAuthModal);
    $('auth-modal').addEventListener('click', (e) => {
      if (e.target === $('auth-modal')) closeAuthModal();
    });
    document.querySelectorAll('.modal-tab').forEach(tab => {
      tab.addEventListener('click', () => openAuthModal(tab.getAttribute('data-auth-mode')));
    });
    $('auth-form').addEventListener('submit', handleAuthSubmit);
    $('auth-demo-btn').addEventListener('click', loginWithDemo);
  }

  async function init() {
    renderInterestChips();
    renderStoreFilters();
    renderAuthNav();
    bindGlobalEvents();
    await checkBackend();
    await restoreSession();
  }

  init();
})();
