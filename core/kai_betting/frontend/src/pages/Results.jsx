import { useState, useEffect } from 'react';
import { CheckCircle2, XCircle, RotateCcw, AlertTriangle, Trophy } from 'lucide-react';
import { api } from '../lib/api';

const OUTCOME_ICONS = {
  won: CheckCircle2, lost: XCircle, push: RotateCcw, void: AlertTriangle,
};

const OUTCOME_COLORS = {
  won: 'text-emerald-400 bg-emerald-600/10',
  lost: 'text-red-400 bg-red-600/10',
  push: 'text-amber-400 bg-amber-600/10',
  void: 'text-surface-400 bg-surface-600/10',
};

export default function Results() {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState({ wins: 0, total: 0, winRate: 0 });

  useEffect(() => {
    api.predictions({ status: undefined, limit: 50 })
      .then((data) => {
        const settled = (Array.isArray(data) ? data : []).filter(
          (p) => ['won', 'lost', 'push', 'void'].includes(p.status)
        );
        setResults(settled);
        const wins = settled.filter((p) => p.status === 'won').length;
        setStats({
          wins,
          total: settled.length,
          push: settled.filter((p) => p.status === 'push').length,
          lost: settled.filter((p) => p.status === 'lost').length,
          winRate: settled.length > 0 ? ((wins / settled.length) * 100).toFixed(1) : 0,
        });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Trophy className="w-6 h-6 text-accent-amber" />
          Results
        </h1>
        <p className="text-surface-400 text-sm mt-1">Settled prediction outcomes</p>
      </div>

      {/* Stats summary */}
      {stats.total > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="card p-4 text-center">
            <p className="stat-value text-white">{stats.total}</p>
            <p className="stat-label">Total</p>
          </div>
          <div className="card p-4 text-center">
            <p className="stat-value text-emerald-400">{stats.wins}</p>
            <p className="stat-label">Won</p>
          </div>
          <div className="card p-4 text-center">
            <p className="stat-value text-red-400">{stats.lost}</p>
            <p className="stat-label">Lost</p>
          </div>
          <div className="card p-4 text-center">
            <p className="stat-value text-white">{stats.winRate}%</p>
            <p className="stat-label">Win Rate</p>
          </div>
        </div>
      )}

      {/* Progress bar */}
      {stats.total > 0 && (
        <div className="card p-4">
          <div className="flex h-3 rounded-full overflow-hidden bg-surface-800">
            <div style={{ width: `${(stats.wins / stats.total) * 100}%` }}
              className="bg-emerald-500 transition-all" />
            <div style={{ width: `${(stats.push / stats.total) * 100}%` }}
              className="bg-amber-500 transition-all" />
            <div style={{ width: `${(stats.lost / stats.total) * 100}%` }}
              className="bg-red-500 transition-all" />
          </div>
          <div className="flex justify-between mt-2 text-xs text-surface-500">
            <span className="text-emerald-400">Won ({stats.wins})</span>
            <span className="text-amber-400">Push ({stats.push})</span>
            <span className="text-red-400">Lost ({stats.lost})</span>
          </div>
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => <div key={i} className="skeleton h-16 rounded-xl" />)}
        </div>
      ) : results.length === 0 ? (
        <div className="card flex flex-col items-center py-16 text-center">
          <CheckCircle2 className="w-12 h-12 text-surface-600 mb-4" />
          <h3 className="text-surface-300 font-medium text-lg">No settled results</h3>
          <p className="text-surface-500 text-sm mt-1">Results appear when predictions are settled</p>
        </div>
      ) : (
        <div className="space-y-2">
          {results.map((r) => {
            const Icon = OUTCOME_ICONS[r.status] || AlertTriangle;
            return (
              <div key={r.id} className="card p-4 flex items-center gap-4">
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${OUTCOME_COLORS[r.status] || ''}`}>
                  <Icon className="w-5 h-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-brand-400 uppercase">{r.sport_key}</span>
                    <span className="text-xs text-surface-500">•</span>
                    <span className="text-sm text-surface-300">{r.market_name}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="font-semibold text-white">{r.selection?.toUpperCase()}</span>
                    {r.bookmaker_odds && (
                      <span className="font-mono text-xs text-surface-500">@{r.bookmaker_odds.toFixed(2)}</span>
                    )}
                    <span className="text-sm text-surface-400 ml-2">{r.outcome?.toUpperCase()}</span>
                  </div>
                </div>
                <span className={`badge ${OUTCOME_COLORS[r.status] || ''}`}>
                  {r.status}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
