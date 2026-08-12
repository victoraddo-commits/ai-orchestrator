import { TrendingUp, TrendingDown, AlertTriangle, Clock } from 'lucide-react';

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

function formatStartTime(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return '';
  return dt.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export default function PredictionCard({ prediction: p, showActions = false }) {
  const level = confidenceLevel(p.confidence);
  const confColor = CONFIDENCE_COLORS[level];
  const hasTeams = !!(p.home_team && p.away_team);
  const startTime = formatStartTime(p.event_time);

  return (
    <div className="card p-4 hover:border-surface-700 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          {/* Teams + start time */}
          {hasTeams && (
            <div className="font-semibold text-white text-lg leading-tight">
              {p.home_team} <span className="text-surface-500 font-normal">vs</span> {p.away_team}
            </div>
          )}

          <div className="flex items-center flex-wrap gap-2 mt-1">
            <span className="text-xs font-semibold text-brand-400 uppercase tracking-wider">
              {p.sport_key}
            </span>
            {(p.league_name || p.league_key) && (
              <>
                <span className="text-xs text-surface-500">•</span>
                <span className="text-xs text-surface-500">{p.league_name || p.league_key}</span>
              </>
            )}
            {startTime && (
              <>
                <span className="text-xs text-surface-500">•</span>
                <span className="text-xs text-emerald-400 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {startTime}
                </span>
              </>
            )}
          </div>

          <div className="flex items-center gap-2 mt-2">
            <span className="text-sm font-medium text-surface-200">
              {p.market_name}
            </span>
            <span className="font-semibold text-white">
              {p.selection?.toUpperCase()}
            </span>
            {p.bookmaker_odds && (
              <span className="text-sm font-mono text-surface-400 bg-surface-800 px-2 py-0.5 rounded">
                @{p.bookmaker_odds.toFixed(2)}
              </span>
            )}
            {p.line != null && !String(p.market_name || '').includes(String(p.line)) && (
              <span className="text-xs font-mono text-surface-400">({p.line})</span>
            )}
          </div>

          {p.reasoning && (
            <p className="text-xs text-surface-500 mt-1.5">{p.reasoning}</p>
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
