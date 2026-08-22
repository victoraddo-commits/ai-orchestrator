import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { api, setAuthToken } from '../lib/api';

const AuthContext = createContext(null);

const STORAGE_KEY = 'kai_betting_user';

export function AuthProvider({ children }) {
  const [session, setSession] = useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : null;
    } catch { return null; }
  });

  useEffect(() => {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else localStorage.removeItem(STORAGE_KEY);
    setAuthToken(session?.token ?? null);
  }, [session]);

  const login = useCallback(async (email, password) => {
    const data = await api.login(email, password);
    setSession(data);
    return data;
  }, []);

  const register = useCallback(async (email, password) => {
    const data = await api.register(email, password);
    return data;
  }, []);

  const logout = useCallback(async () => {
    await api.logout().catch(() => {});
    setSession(null);
  }, []);

  return (
    <AuthContext.Provider value={{
      user: session?.user ?? null,
      login,
      register,
      logout,
      isAdmin: session?.user?.is_admin === 1,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
