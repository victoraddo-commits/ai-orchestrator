import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, TrendingUp, Target, CheckCircle2,
  BarChart3, CreditCard, User, Shield, Sparkles,
} from 'lucide-react';

const NAV = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/predictions', icon: TrendingUp, label: 'Predictions' },
  { to: '/odds', icon: Target, label: 'Odds Groups' },
  { to: '/results', icon: CheckCircle2, label: 'Results' },
  { to: '/performance', icon: BarChart3, label: 'Performance' },
  { to: '/subscribe', icon: CreditCard, label: 'Subscribe' },
  { to: '/account', icon: User, label: 'Account' },
  { to: '/admin', icon: Shield, label: 'Admin' },
];

export default function Sidebar() {
  return (
    <aside className="w-64 bg-surface-900 border-r border-surface-800 flex flex-col flex-shrink-0">
      <div className="p-6 border-b border-surface-800">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-white">Kai Betting</h1>
            <p className="text-xs text-surface-400">AI Predictions</p>
          </div>
        </div>
      </div>
      <nav className="flex-1 py-4 px-3 space-y-1 overflow-y-auto">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
               transition-colors duration-150 ${
                isActive
                  ? 'bg-brand-600/20 text-brand-400'
                  : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'
              }`
            }
          >
            <Icon className="w-5 h-5" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="p-4 border-t border-surface-800">
        <div className="flex items-center gap-2 text-xs text-surface-500">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          AI Engine Active
        </div>
      </div>
    </aside>
  );
}
