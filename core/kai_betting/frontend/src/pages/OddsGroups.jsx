import { useState, useEffect } from 'react';
import { Target, Shield, Flame, Zap, Diamond, ChevronDown, ChevronUp } from 'lucide-react';
import { api } from '../lib/api';

const RISK_ICONS = {
  conservative: Shield,
  moderate: Flame,
  aggressive: Zap,
  high_risk: Diamond,
};

const RISK_COLORS = {
  conservative: 'text-emerald-400',
  moderate: 'text-amber-400',
  aggressive: 'text-orange-400',
  high_risk: 'text-red-400',
};

export default function OddsGroups() {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    api.oddsGroups('active', 20)
      .then((d) => setGroups(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
          <Target className="w-5 h-5 sm:w-6 sm:h-6 text-accent-amber" />
          Odds Groups
        </h1>
        <p className="text-surface-400 text-xs sm:text-sm mt-1">Multi-selection accumulators by risk level</p>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-20 rounded-xl" />)}
        </div>
      ) : groups.length === 0 ? (
        <div className="card flex flex-col items-center py-16 text-center">
          <Target className="w-12 h-12 text-surface-600 mb-4" />
          <h3 className="text-surface-300 font-medium text-lg">No active odds groups</h3>
          <p className="text-surface-500 text-sm mt-1">Check back soon for new accumulators</p>
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => {
            const RiskIcon = RISK_ICONS[g.risk_level] || Target;
            const isExpanded = expanded === g.id;
            return (
              <div key={g.id} className="card hover:border-surface-700 transition-colors">
                <button
                  className="w-full flex items-center justify-between p-4 text-left"
                  onClick={() => setExpanded(isExpanded ? null : g.id)}
                >
                  <div className="flex items-center gap-4">
                    <div className={`w-10 h-10 rounded-lg bg-surface-800 flex items-center justify-center ${RISK_COLORS[g.risk_level] || 'text-surface-400'}`}>
                      <RiskIcon className="w-5 h-5" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-white">{g.label}</h3>
                      <p className="text-sm text-surface-400">
                        {g.num_selections} selections • {g.risk_level?.replace('_', ' ')}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 sm:gap-6">
                    <div className="text-right">
                      <p className="text-base sm:text-lg font-bold text-white font-mono">{g.combined_odds?.toFixed(2)}</p>
                      <p className="text-[10px] sm:text-xs text-surface-500">Odds</p>
                    </div>
                    <div className="hidden sm:block text-right">
                      <p className="text-sm font-semibold text-white">{g.average_confidence?.toFixed(0)}%</p>
                      <p className="text-xs text-surface-500">Avg conf</p>
                    </div>
                    {isExpanded ? <ChevronUp className="w-5 h-5 text-surface-500" /> : <ChevronDown className="w-5 h-5 text-surface-500" />}
                  </div>
                </button>
                {isExpanded && (
                  <div className="border-t border-surface-800 px-4 py-3 space-y-2">
                    {g.selections?.map((s, i) => (
                      <div key={i} className="flex items-center justify-between text-sm">
                        <div>
                          <span className="text-surface-300">{s.sport_key}</span>
                          <span className="text-surface-500 mx-2">•</span>
                          <span className="text-surface-400">{s.market_name}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-white font-semibold">{s.selection?.toUpperCase()}</span>
                          {s.bookmaker_odds && (
                            <span className="font-mono text-brand-400">@{s.bookmaker_odds.toFixed(2)}</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
