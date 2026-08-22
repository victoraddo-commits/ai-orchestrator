import { useState, useEffect } from 'react';
import { TrendingUp, Filter, Search, RefreshCw } from 'lucide-react';
import PredictionCard from '../components/PredictionCard';
import { api } from '../lib/api';

const SPORTS = [
  { key: '', label: 'All Sports' },
  { key: 'football', label: '⚽ Football' },
  { key: 'basketball', label: '🏀 Basketball' },
  { key: 'tennis', label: '🎾 Tennis' },
  { key: 'baseball', label: '⚾ Baseball' },
  { key: 'ice_hockey', label: '🏒 Ice Hockey' },
  { key: 'american_football', label: '🏈 American Football' },
];

const STATUSES = [
  { key: '', label: 'All Status' },
  { key: 'published', label: 'Published' },
  { key: 'pending', label: 'Pending' },
  { key: 'won', label: 'Won' },
  { key: 'lost', label: 'Lost' },
];

export default function Predictions() {
  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sport, setSport] = useState('');
  const [status, setStatus] = useState('published');
  const [page, setPage] = useState(0);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.predictions({
        sport_key: sport || undefined,
        status: status || undefined,
        limit: 20,
        offset: page * 20,
      });
      setPredictions(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [sport, status, page]);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <TrendingUp className="w-5 h-5 sm:w-6 sm:h-6 text-brand-400" />
            Predictions
          </h1>
          <p className="text-surface-400 text-xs sm:text-sm mt-1">AI-generated sports predictions</p>
        </div>
        <button onClick={load} className="btn-ghost" title="Refresh">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Filters */}
      <div className="card p-3 sm:p-4 space-y-3">
        {/* Sport filter — horizontally scrollable */}
        <div className="flex items-center gap-2 overflow-x-auto pb-0.5 scrollbar-none">
          <Filter className="w-4 h-4 text-surface-500 flex-shrink-0" />
          {SPORTS.map((s) => (
            <button
              key={s.key}
              onClick={() => { setSport(s.key); setPage(0); }}
              className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap flex-shrink-0 ${
                sport === s.key
                  ? 'bg-brand-600 text-white'
                  : 'bg-surface-800 text-surface-400 hover:text-surface-200 hover:bg-surface-700'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        {/* Status filter */}
        <div className="flex items-center gap-2 overflow-x-auto scrollbar-none">
          {STATUSES.map((s) => (
            <button
              key={s.key}
              onClick={() => { setStatus(s.key); setPage(0); }}
              className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap flex-shrink-0 ${
                status === s.key
                  ? 'bg-brand-600 text-white'
                  : 'bg-surface-800 text-surface-400 hover:text-surface-200 hover:bg-surface-700'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="card border-red-600/30 bg-red-600/5 text-red-400 text-sm p-4">{error}</div>
      )}

      {/* List */}
      {loading ? (
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
      ) : predictions.length === 0 ? (
        <div className="card flex flex-col items-center py-16 text-center">
          <TrendingUp className="w-12 h-12 text-surface-600 mb-4" />
          <h3 className="text-surface-300 font-medium text-lg">No predictions found</h3>
          <p className="text-surface-500 text-sm mt-1">Try adjusting your filters or check back later</p>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {predictions.map((p) => <PredictionCard key={p.id} prediction={p} />)}
          </div>
          <div className="flex items-center justify-between pt-2">
            <span className="text-sm text-surface-500">{predictions.length} results</span>
            <div className="flex gap-2">
              <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
                className="btn-ghost text-sm disabled:opacity-30">← Previous</button>
              <button onClick={() => setPage(p => p + 1)}
                className="btn-ghost text-sm"
                disabled={predictions.length < 20}>Next →</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
