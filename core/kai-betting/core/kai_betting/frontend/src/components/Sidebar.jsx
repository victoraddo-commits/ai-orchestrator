import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, TrendingUp, Target, CheckCircle2,
  BarChart3, CreditCard, User, Shield, Sparkles, X,
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

export default function Sidebar({ open, onClose }) {
  const navContent = (
    <>
      <div className="p-6 border-b border-surface-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center flex-shrink-0">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-white">Kai Betting</h1>
            <p className="text-xs text-surface-400">AI Predictions</p>
          </div>
        </div>
        {/* Close button — visible only on mobile overlay */}
        <button
          onClick={onClose}
          className="lg:hidden btn-ghost p-1.5 -mr-1"
          title="Close menu"
        >
          <X className="w-5 h-5" />
        </button>
      </div>
      <nav className="flex-1 py-4 px-3 space-y-1 overflow-y-auto">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            onClick={onClose}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
               transition-colors duration-150 ${
                isActive
                  ? 'bg-brand-600/20 text-brand-400'
                  : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'
              }`
            }
          >
            <Icon className="w-5 h-5 flex-shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="p-4 border-t border-surface-800">
        <div className="flex items-center gap-2 text-xs text-surface-500">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse flex-shrink-0" />
          AI Engine Active
        </div>
      </div>
    </>
  );

  return (
    <>
      {/* Mobile overlay backdrop */}
      {open && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity"
          onClick={onClose}
        />
      )}

      {/* Desktop sidebar — always visible */}
      <aside className="hidden lg:flex w-64 bg-surface-900 border-r border-surface-800 flex-col flex-shrink-0">
        {navContent}
      </aside>

      {/* Mobile sidebar — slide-in overlay */}
      <aside
        className={`lg:hidden fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] bg-surface-900 border-r border-surface-800 flex flex-col
          transform transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : '-translate-x-full'}`}
      >
        {navContent}
      </aside>
    </>
  );
}
