import { useAuth } from '../hooks/useAuth';
import { LogOut, Menu } from 'lucide-react';

export default function TopBar({ onMenuToggle }) {
  const { user, logout } = useAuth();

  return (
    <header className="h-14 sm:h-16 bg-surface-900/80 backdrop-blur border-b border-surface-800 flex items-center justify-between px-4 sm:px-6 flex-shrink-0">
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuToggle}
          className="lg:hidden btn-ghost p-1.5 -ml-1"
          title="Menu"
        >
          <Menu className="w-5 h-5" />
        </button>
        <div>
          {user && (
            <p className="text-xs sm:text-sm text-surface-400">
              Welcome, <span className="text-surface-200 font-semibold">{user.full_name || user.email}</span>
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 sm:gap-4">
        {user && (
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="hidden sm:block text-right">
              <p className="text-sm font-medium text-surface-200">{user.email}</p>
              {user.is_admin === 1 && (
                <p className="text-xs text-brand-400">Admin</p>
              )}
            </div>
            <button
              onClick={logout}
              className="btn-ghost p-1.5 sm:p-2"
              title="Logout"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
