import { useState, useEffect } from 'react';
import {
  BarChart3, TrendingUp, TrendingDown, DollarSign, Activity,
  PieChart, Target,
} from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart as ReBarChart, Bar, PieChart as RePieChart, Pie, Cell, Legend } from 'recharts';
import StatCard from '../components/StatCard';
import { api } from '../lib/api';

const COLORS = { won: '#16a34a', lost: '#dc2626', push: '#ca8a04', void: '#64748b' };

export default function Performance() {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.performance({ period: 'all_time' }),
      api.performance({ period: 'monthly', days: 30 }),
    ])
      .then(([all, monthly]) => setMetrics({ all, monthly }))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto space-y-6 animate-pulse">
        <div className="skeleton h-10 w-48" />
        <div className="grid grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-28 rounded-xl" />)}
        </div>
        <div className="skeleton h-64 rounded-xl" />
      </div>
    );
  }

  const all = metrics?.all || {};
  const monthly = metrics?.monthly || {};

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <BarChart3 className="w-6 h-6 text-brand-400" />
          Performance Analytics
        </h1>
        <p className="text-surface-400 text-sm mt-1">Prediction accuracy, ROI, and calibration tracking</p>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Target} label="Win Rate" value={`${all.win_rate ?? 0}%`} trend={all.win_rate} />
        <StatCard icon={DollarSign} label="ROI" value={`${all.roi ?? 0}%`} trend={all.roi} />
        <StatCard icon={Activity} label="Total Picks" value={all.total_predictions ?? 0} />
        <StatCard icon={TrendingUp} label="Profit/Loss" value={`${(all.profit_loss ?? 0).toFixed(2)}u`}
          trend={all.profit_loss} />
      </div>

      {/* Win/Loss breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Pie Chart */}
        <div className="card">
          <h3 className="text-sm font-semibold text-surface-300 mb-4">Outcome Distribution</h3>
          {all.total_predictions > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <RePieChart>
                <Pie
                  data={[
                    { name: 'Won', value: all.wins || 0 },
                    { name: 'Lost', value: all.losses || 0 },
                    { name: 'Push', value: all.pushes || 0 },
                    { name: 'Void', value: all.voids || 0 },
                  ].filter((d) => d.value > 0)}
                  cx="50%" cy="50%" innerRadius={60} outerRadius={90}
                  paddingAngle={2} dataKey="value"
                >
                  {['won', 'lost', 'push', 'void'].map((key) => (
                    <Cell key={key} fill={COLORS[key]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }}
                  labelStyle={{ color: '#e2e8f0' }}
                />
                <Legend />
              </RePieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-surface-500 text-sm text-center py-12">No data yet</p>
          )}
        </div>

        {/* ROI Gauge */}
        <div className="card">
          <h3 className="text-sm font-semibold text-surface-300 mb-4">Performance Summary</h3>
          <div className="space-y-4">
            <div className="flex justify-between text-sm">
              <span className="text-surface-400">Average Odds</span>
              <span className="text-white font-mono font-semibold">{all.average_odds?.toFixed(2)}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-surface-400">Avg Confidence</span>
              <span className="text-white font-mono font-semibold">{all.average_confidence?.toFixed(0)}%</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-surface-400">Win/Loss Ratio</span>
              <span className="text-white font-mono font-semibold">
                {all.losses > 0 ? (all.wins / all.losses).toFixed(2) : '∞'}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-surface-400">Strike Rate</span>
              <span className={`font-mono font-semibold ${(all.win_rate || 0) >= 50 ? 'text-emerald-400' : 'text-red-400'}`}>
                {all.win_rate?.toFixed(1)}%
              </span>
            </div>
          </div>

          <div className="mt-6 p-4 rounded-lg bg-surface-800">
            <p className="text-xs text-surface-400 mb-1">ROI</p>
            <p className={`text-2xl font-bold ${(all.roi || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {all.roi >= 0 ? '+' : ''}{all.roi?.toFixed(2)}%
            </p>
            <p className="text-xs text-surface-500 mt-1">Return on investment (1 unit stake per pick)</p>
          </div>
        </div>
      </div>

      {/* By Sport */}
      {all.by_sport?.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-surface-300 mb-4">Performance by Sport</h3>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="table-header">Sport</th>
                  <th className="table-header text-right">Picks</th>
                  <th className="table-header text-right">Wins</th>
                  <th className="table-header text-right">Win Rate</th>
                  <th className="table-header text-right">Progress</th>
                </tr>
              </thead>
              <tbody>
                {all.by_sport.map((s) => (
                  <tr key={s.sport_key}>
                    <td className="table-cell text-surface-200 font-medium">{s.sport_name}</td>
                    <td className="table-cell text-right font-mono">{s.total}</td>
                    <td className="table-cell text-right font-mono text-emerald-400">{s.wins}</td>
                    <td className="table-cell text-right font-mono">{s.win_rate}%</td>
                    <td className="table-cell">
                      <div className="w-24 h-2 rounded-full bg-surface-800 ml-auto">
                        <div
                          className="h-full rounded-full bg-brand-500 transition-all"
                          style={{ width: `${s.win_rate}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
