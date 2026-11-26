/**
 * Capa de autenticación y persistencia con adaptadores limpios:
 * - ApiAdapter: servidor local Python + SQLite (sin fallback fantasma ante 401)
 * - LocalAdapter: modo Vercel Cloud / offline usando localStorage con contraseñas hasheadas
 */

import { hashLocalPassword, verifyLocalPassword } from './utils.js';

const STORAGE_KEYS = {
  USERS: 'regalacl_users_v1',
  SESSION: 'regalacl_session_v1'
};

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

function readSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEYS.SESSION) || 'null');
  } catch {
    return null;
  }
}

function writeSession(session) {
  if (!session) localStorage.removeItem(STORAGE_KEYS.SESSION);
  else localStorage.setItem(STORAGE_KEYS.SESSION, JSON.stringify(session));
}

class LocalAdapter {
  constructor(catalog) {
    this.catalog = catalog;
  }

  async ensureDemo() {
    const users = loadLocalUsers();
    if (!users['demo@regalacl.cl']) {
      users['demo@regalacl.cl'] = {
        name: 'Usuario Demo',
        email: 'demo@regalacl.cl',
        passwordHash: await hashLocalPassword('demo'),
        savedGifts: this.catalog[9]
          ? [{ ...this.catalog[9], savedAt: new Date().toISOString(), note: 'Idea para cumpleaños de papá' }]
          : [],
        savedSearches: []
      };
      saveLocalUsers(users);
    }
  }

  async restoreSession() {
    await this.ensureDemo();
    const session = readSession();
    if (!session?.email) return null;
    const users = loadLocalUsers();
    return users[session.email] ? { ...users[session.email] } : null;
  }

  async auth(mode, { name, email, password }) {
    await this.ensureDemo();
    const users = loadLocalUsers();
    if (mode === 'register') {
      if (users[email]) return { ok: false, error: 'Ya existe una cuenta registrada con este correo.' };
      users[email] = {
        name: name || email.split('@')[0],
        email,
        passwordHash: await hashLocalPassword(password),
        savedGifts: [],
        savedSearches: []
      };
      saveLocalUsers(users);
      writeSession({ email });
      return { ok: true, user: { ...users[email] } };
    }
    const existing = users[email];
    const valid = await verifyLocalPassword(password, existing);
    if (!existing || !valid) return { ok: false, error: 'Correo o contraseña incorrectos.' };
    if (existing.password && !existing.passwordHash) {
      existing.passwordHash = await hashLocalPassword(password);
      delete existing.password;
      saveLocalUsers(users);
    }
    writeSession({ email });
    return { ok: true, user: { ...existing } };
  }

  async loginDemo() {
    await this.ensureDemo();
    const users = loadLocalUsers();
    writeSession({ email: 'demo@regalacl.cl' });
    return { ok: true, user: { ...users['demo@regalacl.cl'] } };
  }

  async logout() {
    writeSession(null);
  }

  async saveGift(user, gift) {
    this._persistUser(user);
    return { ok: true };
  }

  async removeGift(user, _giftId) {
    this._persistUser(user);
    return { ok: true };
  }

  async updateNote(user, _giftId, _note) {
    this._persistUser(user);
    return { ok: true };
  }

  async saveSearch(user, _entry) {
    this._persistUser(user);
    return { ok: true };
  }

  _persistUser(user) {
    const users = loadLocalUsers();
    users[user.email] = {
      ...(users[user.email] || {}),
      name: user.name,
      email: user.email,
      savedGifts: user.savedGifts || [],
      savedSearches: user.savedSearches || []
    };
    saveLocalUsers(users);
  }
}

class ApiAdapter {
  async restoreSession() {
    const session = readSession();
    if (!session?.token) return null;
    try {
      const res = await fetch('/api/me', {
        headers: { Authorization: 'Bearer ' + session.token }
      });
      if (res.status === 401) {
        // Sesión expirada o revocada: limpiar y NO resucitar desde localStorage
        writeSession(null);
        return null;
      }
      if (!res.ok) return null;
      const data = await res.json();
      return { ...data.user, token: session.token };
    } catch {
      return null;
    }
  }

  async auth(mode, { name, email, password }) {
    const endpoint = mode === 'register' ? '/api/register' : '/api/login';
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name || email.split('@')[0], email, password })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return { ok: false, error: data.error || 'Error de autenticación.' };
      writeSession({ email: data.user.email, token: data.token });
      return { ok: true, user: { ...data.user, token: data.token } };
    } catch {
      return { ok: false, error: 'No se pudo conectar con el servidor.' };
    }
  }

  async loginDemo() {
    return this.auth('login', { email: 'demo@regalacl.cl', password: 'demo' });
  }

  async logout(user) {
    if (user?.token) {
      fetch('/api/logout', {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + user.token }
      }).catch(() => {});
    }
    writeSession(null);
  }

  async _authedFetch(url, method, token, body) {
    try {
      const res = await fetch(url, {
        method,
        headers: {
          ...(body ? { 'Content-Type': 'application/json' } : {}),
          Authorization: 'Bearer ' + token
        },
        ...(body ? { body: JSON.stringify(body) } : {})
      });
      if (res.status === 401) {
        writeSession(null);
        return { ok: false, expired: true };
      }
      return { ok: res.ok };
    } catch {
      return { ok: false };
    }
  }

  async saveGift(user, gift) {
    return this._authedFetch('/api/saved-gifts', 'POST', user.token, { gift });
  }

  async removeGift(user, giftId) {
    return this._authedFetch('/api/saved-gifts/' + encodeURIComponent(giftId), 'DELETE', user.token);
  }

  async updateNote(user, giftId, note) {
    return this._authedFetch('/api/saved-gifts/' + encodeURIComponent(giftId) + '/note', 'PUT', user.token, { note });
  }

  async saveSearch(user, entry) {
    return this._authedFetch('/api/saved-searches', 'POST', user.token, { search: entry });
  }
}

export async function detectBackendAndCreateStore(catalog) {
  try {
    const res = await fetch('/api/health', { signal: AbortSignal.timeout(2500) });
    if (res.ok) {
      const info = await res.json().catch(() => ({}));
      const backendAuth = info.auth !== 'local';
      return {
        backendOnline: true,
        backendAuth,
        mode: info.mode || 'local_sqlite',
        store: backendAuth ? new ApiAdapter() : new LocalAdapter(catalog)
      };
    }
  } catch {
    // offline / standalone
  }
  return {
    backendOnline: false,
    backendAuth: false,
    mode: 'offline',
    store: new LocalAdapter(catalog)
  };
}
