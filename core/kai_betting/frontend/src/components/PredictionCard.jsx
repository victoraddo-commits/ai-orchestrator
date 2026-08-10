import { TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react';

const CONFIDENCE_COLORS = {
  high: 'text-emerald-400 bg-emerald-600/10 border-emerald-600/20',
  medium: 'text-amber-400 bg-amber-600/10 border-amber-600/20',
  low: 'text-red-400 bg-red-600/10 border-red-600/20',
};

function confidenceLevel(val) {
  if (val >= 70) return 'high';
  if (val >= 50) return 'medium';
  return 'low';
}

export default function PredictionCard({ prediction: p, showActions = false }) {
  const level = confidenceLevel(p.confidence);
  const confColor = CONFIDENCE_COLORS[level];

  return (
    <div className="card p-4 hover:border-surface-700 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-semibold text-brand-400 uppercase tracking-wider">
              {p.sport_key}
            </span>
            <span className="text-xs text-surface-500">•</span>
            <span className="text-xs text-surface-400">{p.market_name}</span>
            {p.league_key && (
              <>
                <span className="text-xs text-surface-500">•</span>
                <span className="text-xs text-surface-500">{p.league_key}</span>
              </>
            )}
          </div>

          <div className="flex items-center gap-2 mt-1">
            <span className="font-semibold text-white text-lg">
              {p.selection?.toUpperCase()}
            </span>
            {p.bookmaker_odds && (
              <span className="text-sm font-mono text-surface-400 bg-surface-800 px-2 py-0.5 rounded">
                @{p.bookmaker_odds.toFixed(2)}
              </span>
            )}
          </div>

          {p.reasoning && (
            <p className="text-xs text-surface-500 mt-1.5 line-clamp-1">{p.reasoning}</p>
          )}
        </div>

        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          <span className={`badge border ${confColor}`}>
            {p.confidence?.toFixed(0)}% conf
          </span>
          {p.edge != null && (
            <span className={`text-xs font-medium flex items-center gap-1 ${p.edge > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {p.edge > 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
              {p.edge > 0 ? '+' : ''}{(p.edge * 100).toFixed(1)}% edge
            </span>
          )}
          {p.risk_score > 60 && (
            <span className="text-xs text-amber-400 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />
              Risk: {p.risk_score?.toFixed(0)}
            </span>
          )}
        </div>
      </div>

      {/* Tags */}
      {p.tags && (
        <div className="flex gap-1.5 mt-3 flex-wrap">
          {String(p.tags).split(',').filter(Boolean).map((tag) => (
            <span key={tag} className="text-[11px] text-surface-500 bg-surface-800 px-2 py-0.5 rounded-full">
              {tag.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}

      {/* Status badge */}
      {p.status && p.status !== 'published' && (
        <div className="mt-2 flex items-center gap-2">
          <span className={`badge ${statusBadge(p.status)}`}>{p.status}</span>
        </div>
      )}
    </div>
  );
}

function statusBadge(status) {
  const map = {
    won: 'badge-win', lost: 'badge-lose', push: 'badge-push',
    void: 'badge-pending', pending: 'badge-pending', published: 'badge-active',
  };
  return map[status] || 'badge-pending';
}
