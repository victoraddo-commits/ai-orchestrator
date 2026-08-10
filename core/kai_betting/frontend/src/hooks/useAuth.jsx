import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { api } from '../lib/api';

const AuthContext = createContext(null);

const STORAGE_KEY = 'kai_betting_user';

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : null;
    } catch { return null; }
  });

  useEffect(() => {
    if (user) localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    else localStorage.removeItem(STORAGE_KEY);
  }, [user]);

  const login = useCallback(async (email, password) => {
    const data = await api.login(email, password);
    setUser(data);
    return data;
  }, []);

  const register = useCallback(async (email, password) => {
    const data = await api.register(email, password);
    return data;
  }, []);

  const logout = useCallback(() => {
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, login, register, logout, isAdmin: user?.is_admin === 1 }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
