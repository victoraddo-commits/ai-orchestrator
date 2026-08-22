import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { User, Mail, Calendar, CreditCard, Shield, Bell, LogIn } from 'lucide-react';
import { api } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

export default function Account() {
  const { user, login, register, logout } = useAuth();
  const navigate = useNavigate();

  // Login/Register form
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isRegister, setIsRegister] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Subscription
  const [subscription, setSubscription] = useState(null);

  useEffect(() => {
    if (user) {
      api.userSubscription(user.id)
        .then((d) => setSubscription(d))
        .catch(() => {});
    }
  }, [user]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (isRegister) {
        await register(email, password);
        // Auto-login after register
        await login(email, password);
      } else {
        await login(email, password);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  // Not logged in
  if (!user) {
    return (
      <div className="max-w-md mx-auto space-y-6 pt-8">
        <div className="text-center">
          <div className="w-16 h-16 rounded-2xl bg-brand-600/20 flex items-center justify-center mx-auto mb-4">
            <User className="w-8 h-8 text-brand-400" />
          </div>
          <h1 className="text-2xl font-bold text-white mb-1">
            {isRegister ? 'Create Account' : 'Sign In'}
          </h1>
          <p className="text-surface-400 text-sm">
            {isRegister ? 'Join Kai Betting for AI predictions' : 'Access your account'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="card space-y-4">
          {error && (
            <div className="p-3 rounded-lg bg-red-600/10 text-red-400 text-sm border border-red-600/20">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-surface-300 mb-1.5">Email</label>
            <input
              type="email"
              className="input"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-surface-300 mb-1.5">Password</label>
            <input
              type="password"
              className="input"
              placeholder="Min 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
            />
          </div>

          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? 'Please wait...' : isRegister ? 'Create Account' : 'Sign In'}
          </button>

          <p className="text-center text-sm text-surface-500">
            {isRegister ? 'Already have an account?' : "Don't have an account?"}{' '}
            <button
              type="button"
              onClick={() => { setIsRegister(!isRegister); setError(null); }}
              className="text-brand-400 hover:text-brand-300 font-medium"
            >
              {isRegister ? 'Sign In' : 'Register'}
            </button>
          </p>
        </form>
      </div>
    );
  }

  // Logged in
  const hasSub = subscription?.has_subscription;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
          <User className="w-5 h-5 sm:w-6 sm:h-6 text-brand-400" />
          My Account
        </h1>
      </div>

      {/* Profile */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-surface-300 uppercase tracking-wider">Profile</h2>
        <div className="grid grid-cols-2 gap-4">
          <ProfileField icon={Mail} label="Email" value={user.email} />
          <ProfileField icon={User} label="Name" value={user.full_name || 'Not set'} />
          <ProfileField icon={Calendar} label="Joined" value={user.created_at?.slice(0, 10) || 'Unknown'} />
          <ProfileField icon={Shield} label="Role" value={user.is_admin === 1 ? 'Admin' : 'User'} />
        </div>
      </div>

      {/* Subscription */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-surface-300 uppercase tracking-wider">
          <CreditCard className="w-4 h-4 inline mr-1.5" /> Subscription
        </h2>
        {hasSub ? (
          <div className="p-4 rounded-lg bg-brand-600/10 border border-brand-600/20">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-white font-semibold">{subscription.plan_name}</p>
                <p className="text-xs text-surface-400 mt-1">
                  Expires: {subscription.expires_at?.slice(0, 10) || 'N/A'}
                </p>
              </div>
              <span className="badge-active">Active</span>
            </div>
          </div>
        ) : (
          <div className="p-4 rounded-lg bg-surface-800 text-center">
            <p className="text-surface-400 text-sm">You're on the free tier (3 picks/day)</p>
            <button onClick={() => navigate('/subscribe')} className="btn-primary mt-3">
              <CreditCard className="w-4 h-4" /> Upgrade Now
            </button>
          </div>
        )}
      </div>

      {/* Notifications */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-surface-300 uppercase tracking-wider">
          <Bell className="w-4 h-4 inline mr-1.5" /> Notifications
        </h2>
        <Toggle label="Daily picks via Telegram" defaultChecked />
        <Toggle label="Result notifications" defaultChecked />
        <Toggle label="Odds group alerts" />
      </div>

      <button onClick={logout} className="btn-danger w-full">
        <LogIn className="w-4 h-4" /> Sign Out
      </button>
    </div>
  );
}

function ProfileField({ icon: Icon, label, value }) {
  return (
    <div>
      <p className="text-xs text-surface-500 mb-0.5 flex items-center gap-1">
        <Icon className="w-3 h-3" /> {label}
      </p>
      <p className="text-sm text-white font-medium">{value}</p>
    </div>
  );
}

function Toggle({ label, defaultChecked = false }) {
  const [on, setOn] = useState(defaultChecked);
  return (
    <label className="flex items-center justify-between cursor-pointer">
      <span className="text-sm text-surface-300">{label}</span>
      <button
        role="switch"
        aria-checked={on}
        onClick={() => setOn(!on)}
        className={`w-10 h-6 rounded-full transition-colors relative ${
          on ? 'bg-brand-600' : 'bg-surface-700'
        }`}
      >
        <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
          on ? 'translate-x-[18px]' : 'translate-x-0.5'
        }`} />
      </button>
    </label>
  );
}
