const BASE = '/api/betting';

async function request(path, options = {}) {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || err.detail || `Request failed: ${res.status}`);
  }
  const data = await res.json();
  if (!data.success) throw new Error(data.error || 'Request failed');
  return data.data;
}

export const api = {
  // Health
  health: () => request('/health'),

  // Auth
  register: (email, password) => request('/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) }),
  login: (email, password) => request('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),

  // Sports
  sports: () => request('/sports'),
  leagues: (sportKey) => request(`/sports/${sportKey}/leagues`),
  teams: (sportKey, leagueKey, search) => {
    const params = new URLSearchParams();
    if (leagueKey) params.set('league_key', leagueKey);
    if (search) params.set('search', search);
    return request(`/sports/${sportKey}/teams?${params}`);
  },

  // Events
  events: (opts = {}) => {
    const params = new URLSearchParams();
    if (opts.sport_key) params.set('sport_key', opts.sport_key);
    if (opts.status) params.set('status', opts.status);
    if (opts.date) params.set('date', opts.date);
    if (opts.limit) params.set('limit', opts.limit);
    if (opts.offset) params.set('offset', opts.offset);
    return request(`/events?${params}`);
  },

  // Predictions
  predictions: (opts = {}) => {
    const params = new URLSearchParams();
    if (opts.status) params.set('status', opts.status);
    if (opts.sport_key) params.set('sport_key', opts.sport_key);
    if (opts.market_type) params.set('market_type', opts.market_type);
    if (opts.limit) params.set('limit', opts.limit);
    if (opts.offset) params.set('offset', opts.offset);
    return request(`/predictions?${params}`);
  },
  prediction: (id) => request(`/predictions/${id}`),
  generatePrediction: (data) => request('/predictions/generate', { method: 'POST', body: JSON.stringify(data) }),
  settlePrediction: (id, outcome) => request(`/predictions/${id}/settle`, { method: 'POST', body: JSON.stringify(outcome) }),

  // Odds Groups
  oddsGroups: (status = 'active', limit = 20) => request(`/odds-groups?status=${status}&limit=${limit}`),
  oddsGroup: (id) => request(`/odds-groups/${id}`),
  generateOddsGroup: (data) => request('/odds-groups/generate', { method: 'POST', body: JSON.stringify(data) }),

  // Subscriptions
  plans: () => request('/plans'),
  purchaseSubscription: (userId, data) => request(`/subscriptions/purchase?user_id=${userId}`, { method: 'POST', body: JSON.stringify(data) }),
  userSubscription: (userId) => request(`/subscriptions/${userId}`),

  // Performance
  performance: (opts = {}) => {
    const params = new URLSearchParams();
    if (opts.period) params.set('period', opts.period);
    if (opts.sport_key) params.set('sport_key', opts.sport_key);
    if (opts.days) params.set('days', opts.days);
    return request(`/performance?${params}`);
  },
  dashboard: () => request('/dashboard'),

  // Admin
  adminConfig: () => request('/admin/config'),
  updateConfig: (key, value) => request('/admin/config', { method: 'PUT', body: JSON.stringify({ key, value }) }),
  adminUsers: (opts = {}) => {
    const params = new URLSearchParams();
    if (opts.search) params.set('search', opts.search);
    if (opts.is_active !== undefined) params.set('is_active', opts.is_active);
    if (opts.limit) params.set('limit', opts.limit);
    return request(`/admin/users?${params}`);
  },
  adminAudit: (opts = {}) => {
    const params = new URLSearchParams();
    if (opts.entity_type) params.set('entity_type', opts.entity_type);
    if (opts.limit) params.set('limit', opts.limit);
    return request(`/admin/audit?${params}`);
  },
};
