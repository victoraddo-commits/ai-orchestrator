export default function StatCard({ icon: Icon, label, value, trend, className = '' }) {
  return (
    <div className={`card-hover ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="stat-label">{label}</span>
        {Icon && <Icon className="w-5 h-5 text-surface-500" />}
      </div>
      <div className="stat-value text-white">{value}</div>
      {trend !== undefined && (
        <p className={`text-xs font-medium mt-2 ${trend >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
          {trend >= 0 ? '↑' : '↓'} {Math.abs(trend)}%
        </p>
      )}
    </div>
  );
}
