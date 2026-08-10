import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  TrendingUp, Target, Users, DollarSign,
  BarChart3, Sparkles, AlertCircle, ArrowRight,
} from 'lucide-react';
import StatCard from '../components/StatCard';
import PredictionCard from '../components/PredictionCard';
import { api } from '../lib/api';

export default function Dashboard() {
  const [dash, setDash] = useState(null);
  const [picks, setPicks] = useState([]);
  const [odds, setOdds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    async function load() {
      try {
        const [dashData, picksData, oddsData] = await Promise.all([
          api.dashboard().catch(() => null),
          api.predictions({ status: 'published', limit: 6 }).catch(() => []),
          api.oddsGroups('active', 3).catch(() => []),
        ]);
        setDash(dashData);
        setPicks(Array.isArray(picksData) ? picksData : []);
        setOdds(Array.isArray(oddsData) ? oddsData : []);
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <DashboardSkeleton />;

  return (
    <div className="max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <Sparkles className="w-8 h-8 text-brand-400" />
            Kai Betting
          </h1>
          <p className="text-surface-400 mt-1">AI-powered sports predictions across 10 sports</p>
        </div>
      </div>

      {error && (
        <div className="card border-red-600/30 bg-red-600/5 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-red-400" />
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={TrendingUp} label="Predictions" value={dash?.total_predictions ?? 0} />
        <StatCard icon={Target} label="Win Rate" value={`${dash?.overall_win_rate ?? 0}%`}
          trend={dash?.overall_win_rate ?? 0} />
        <StatCard icon={Users} label="Subscribers" value={dash?.active_subscriptions ?? 0} />
        <StatCard icon={DollarSign} label="Revenue" value={`GHS ${(dash?.total_revenue ?? 0).toFixed(2)}`} />
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Latest Picks */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-brand-400" />
              Latest Predictions
            </h2>
            <button onClick={() => navigate('/predictions')} className="btn-ghost text-sm">
              View All <ArrowRight className="w-4 h-4" />
            </button>
          </div>
          <div className="space-y-3">
            {picks.length === 0 ? (
              <EmptyState icon={TrendingUp} title="No predictions yet"
                description="Predictions will appear here when published." />
            ) : (
              picks.map((p) => <PredictionCard key={p.id} prediction={p} />)
            )}
          </div>
        </div>

        {/* Side Panel */}
        <div className="space-y-6">
          {/* Odds Groups */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Target className="w-5 h-5 text-accent-amber" />
                Active Odds
              </h2>
              <button onClick={() => navigate('/odds')} className="btn-ghost text-sm">
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-2">
              {odds.length === 0 ? (
                <p className="text-sm text-surface-500">No active odds groups</p>
              ) : (
                odds.map((g) => (
                  <div key={g.id} className="card p-4 hover:border-surface-700 cursor-pointer
                    transition-colors" onClick={() => navigate('/odds')}>
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-white">{g.label}</span>
                      <span className="badge-active">{g.risk_level}</span>
                    </div>
                    <div className="flex gap-4 mt-2 text-xs text-surface-400">
                      <span>Odds: {g.combined_odds?.toFixed(2)}</span>
                      <span>{g.num_selections} picks</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Sports Coverage */}
          {dash?.sports_coverage && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                <BarChart3 className="w-5 h-5 text-brand-400" />
                Sports Coverage
              </h2>
              <div className="card p-4 space-y-2">
                {dash.sports_coverage.slice(0, 6).map((s) => (
                  <div key={s.key} className="flex items-center justify-between text-sm">
                    <span className="text-surface-300">{s.icon} {s.name}</span>
                    <span className="text-surface-500 font-mono">{s.count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Quick Actions */}
          <div>
            <h2 className="text-lg font-semibold text-white mb-3">Quick Actions</h2>
            <div className="space-y-2">
              <button onClick={() => navigate('/predictions')} className="btn-primary w-full justify-center">
                <TrendingUp className="w-4 h-4" /> View Predictions
              </button>
              <button onClick={() => navigate('/subscribe')} className="btn-secondary w-full justify-center">
                <CreditCard className="w-4 h-4" /> Get Premium
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyState({ icon: Icon, title, description }) {
  return (
    <div className="card flex flex-col items-center justify-center py-12 text-center">
      {Icon && <Icon className="w-10 h-10 text-surface-600 mb-3" />}
      <h3 className="text-surface-300 font-medium">{title}</h3>
      <p className="text-surface-500 text-sm mt-1">{description}</p>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="max-w-7xl mx-auto space-y-8 animate-pulse">
      <div className="skeleton h-12 w-64" />
      <div className="grid grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-28 rounded-xl" />)}
      </div>
      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2 space-y-3">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-20 rounded-xl" />)}
        </div>
      </div>
    </div>
  );
}
