import { useState, useEffect } from 'react';
import {
  Shield, Users, Settings, FileText, Search,
  RefreshCw, CheckCircle2, XCircle, Clock,
  UserPlus, UserMinus, Crown,
} from 'lucide-react';
import { api } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

export default function Admin() {
  const { user, isAdmin } = useAuth();
  const [tab, setTab] = useState('overview');
  const [users, setUsers] = useState([]);
  const [config, setConfig] = useState({});
  const [audit, setAudit] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!isAdmin) return;
    async function load() {
      try {
        const [u, c, a] = await Promise.all([
          api.adminUsers({ limit: 100 }),
          api.adminConfig(),
          api.adminAudit({ limit: 50 }),
        ]);
        setUsers(Array.isArray(u) ? u : []);
        setConfig(c || {});
        setAudit(Array.isArray(a) ? a : []);
      } catch (e) {
        console.error('Admin load error:', e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [isAdmin]);

  if (!isAdmin) {
    return (
      <div className="max-w-md mx-auto pt-16 text-center">
        <Shield className="w-16 h-16 text-surface-600 mx-auto mb-4" />
        <h1 className="text-xl font-bold text-white mb-2">Admin Access Required</h1>
        <p className="text-surface-400 text-sm">Sign in with an admin account to access this area.</p>
      </div>
    );
  }

  const filteredUsers = search
    ? users.filter((u) =>
        (u.email || '').toLowerCase().includes(search.toLowerCase()) ||
        (u.full_name || '').toLowerCase().includes(search.toLowerCase()))
    : users;

  const handleUpdateUser = async (userId, updates) => {
    try {
      const res = await fetch(`/api/betting/admin/users/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      const data = await res.json();
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? data.data : u))
      );
    } catch (e) {
      alert(`Update failed: ${e.message}`);
    }
  };

  const TABS = [
    { key: 'overview', label: 'Overview', icon: Shield },
    { key: 'users', label: 'Users', icon: Users },
    { key: 'config', label: 'Config', icon: Settings },
    { key: 'audit', label: 'Audit Log', icon: FileText },
  ];

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Shield className="w-6 h-6 text-brand-400" />
          Admin Dashboard
        </h1>
        <p className="text-surface-400 text-sm mt-1">Manage users, configuration, and audit logs</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-surface-900 rounded-lg p-1 border border-surface-800">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === key
                ? 'bg-brand-600 text-white'
                : 'text-surface-400 hover:text-surface-200'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-16 rounded-xl" />)}
        </div>
      ) : (
        <>
          {/* Overview Tab */}
          {tab === 'overview' && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatCardAdmin label="Total Users" value={users.length} icon={Users} />
              <StatCardAdmin label="Active Subs" value={users.filter((u) => u.subscription_status === 'active').length} icon={CheckCircle2} />
              <StatCardAdmin label="Admins" value={users.filter((u) => u.is_admin === 1).length} icon={Shield} />
              <StatCardAdmin label="Config Keys" value={Object.keys(config).length} icon={Settings} />
            </div>
          )}

          {/* Users Tab */}
          {tab === 'users' && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className="relative flex-1">
                  <Search className="w-4 h-4 text-surface-500 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    className="input pl-10"
                    placeholder="Search users by email or name..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              </div>
              <div className="card overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr>
                        <th className="table-header">User</th>
                        <th className="table-header">Status</th>
                        <th className="table-header">Subscription</th>
                        <th className="table-header">Role</th>
                        <th className="table-header">Joined</th>
                        <th className="table-header">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredUsers.map((u) => (
                        <tr key={u.id} className="hover:bg-surface-800/50">
                          <td className="table-cell">
                            <div>
                              <p className="text-white font-medium">{u.full_name || '—'}</p>
                              <p className="text-xs text-surface-500">{u.email}</p>
                            </div>
                          </td>
                          <td className="table-cell">
                            {u.is_active ? (
                              <span className="badge-active">Active</span>
                            ) : (
                              <span className="badge-pending">Inactive</span>
                            )}
                          </td>
                          <td className="table-cell">
                            {u.subscription_status === 'active' ? (
                              <span className="badge-win">{u.subscription_status}</span>
                            ) : (
                              <span className="text-sm text-surface-500">None</span>
                            )}
                          </td>
                          <td className="table-cell">
                            {u.is_admin === 1 ? (
                              <span className="badge-premium">Admin</span>
                            ) : (
                              <span className="text-sm text-surface-500">User</span>
                            )}
                          </td>
                          <td className="table-cell text-sm text-surface-500">
                            {u.created_at?.slice(0, 10)}
                          </td>
                          <td className="table-cell">
                            <div className="flex items-center gap-1">
                              {u.is_admin === 1 ? (
                                <button
                                  className="btn-icon text-xs text-amber-400 hover:text-amber-300"
                                  title="Revoke admin"
                                  onClick={() => handleUpdateUser(u.id, { is_admin: false })}
                                >
                                  <UserMinus className="w-3.5 h-3.5" />
                                </button>
                              ) : (
                                <button
                                  className="btn-icon text-xs text-brand-400 hover:text-brand-300"
                                  title="Grant admin"
                                  onClick={() => handleUpdateUser(u.id, { is_admin: true })}
                                >
                                  <UserPlus className="w-3.5 h-3.5" />
                                </button>
                              )}
                              {u.subscription_status === 'active' ? (
                                <button
                                  className="btn-icon text-xs text-red-400 hover:text-red-300"
                                  title="Cancel subscription"
                                  onClick={() => handleUpdateUser(u.id, { subscription_status: 'cancelled' })}
                                >
                                  <Crown className="w-3.5 h-3.5" />
                                </button>
                              ) : (
                                <button
                                  className="btn-icon text-xs text-emerald-400 hover:text-emerald-300"
                                  title="Upgrade to premium"
                                  onClick={() => handleUpdateUser(u.id, { subscription_status: 'active' })}
                                >
                                  <Crown className="w-3.5 h-3.5" />
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {filteredUsers.length === 0 && (
                  <p className="text-center text-surface-500 py-8">No users found</p>
                )}
              </div>
            </div>
          )}

          {/* Config Tab */}
          {tab === 'config' && (
            <div className="card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className="table-header">Key</th>
                      <th className="table-header">Value</th>
                      <th className="table-header">Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(config).map(([key, entry]) => (
                      <tr key={key}>
                        <td className="table-cell font-mono text-xs text-brand-400">{key}</td>
                        <td className="table-cell font-mono text-sm text-white">
                          {typeof entry === 'object' ? entry.value : String(entry)}
                        </td>
                        <td className="table-cell text-xs text-surface-500">
                          {typeof entry === 'object' ? entry.updated_at?.slice(0, 10) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Audit Tab */}
          {tab === 'audit' && (
            <div className="space-y-2">
              {audit.length === 0 ? (
                <div className="card text-center py-12 text-surface-500">No audit records yet</div>
              ) : (
                audit.map((entry) => (
                  <div key={entry.id} className="card p-4 flex items-start gap-3">
                    <Clock className="w-4 h-4 text-surface-500 mt-0.5 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-white">{entry.action}</span>
                        <span className="text-xs text-surface-500">{entry.entity_type}</span>
                        {entry.entity_id && (
                          <span className="text-xs text-surface-600">#{entry.entity_id}</span>
                        )}
                      </div>
                      <p className="text-xs text-surface-400 mt-1">
                        {entry.created_at?.slice(0, 19)} — IP: {entry.ip_address || 'N/A'}
                      </p>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatCardAdmin({ label, value, icon: Icon }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="stat-label">{label}</span>
        <Icon className="w-4 h-4 text-surface-500" />
      </div>
      <p className="stat-value text-white">{value}</p>
    </div>
  );
}
