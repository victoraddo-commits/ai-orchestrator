import { useAuth } from '../hooks/useAuth';
import { LogOut } from 'lucide-react';

export default function TopBar() {
  const { user, logout } = useAuth();

  return (
    <header className="h-16 bg-surface-900/80 backdrop-blur border-b border-surface-800 flex items-center justify-between px-6 flex-shrink-0">
      <div>
        {user && (
          <p className="text-sm text-surface-400">
            Welcome back, <span className="text-surface-200 font-semibold">{user.full_name || user.email}</span>
          </p>
        )}
      </div>
      <div className="flex items-center gap-4">
        {user && (
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-sm font-medium text-surface-200">{user.email}</p>
              {user.is_admin === 1 && (
                <p className="text-xs text-brand-400">Admin</p>
              )}
            </div>
            <button
              onClick={logout}
              className="btn-ghost p-2"
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
