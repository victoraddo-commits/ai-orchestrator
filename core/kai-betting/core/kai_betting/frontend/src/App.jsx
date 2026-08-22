import { useState, useCallback } from 'react';
import { Routes, Route, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Predictions from './pages/Predictions';
import OddsGroups from './pages/OddsGroups';
import Results from './pages/Results';
import Performance from './pages/Performance';
import Subscribe from './pages/Subscribe';
import Account from './pages/Account';
import Admin from './pages/Admin';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import { AuthProvider } from './hooks/useAuth';

function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  const toggleSidebar = useCallback(() => setSidebarOpen(v => !v), []);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  // Close sidebar on route change (mobile nav)
  const handleRouteClose = useCallback(() => {
    if (sidebarOpen) setSidebarOpen(false);
  }, [sidebarOpen]);

  // Use key to force sidebar close on route change
  return (
    <div className="flex h-screen overflow-hidden" onClick={handleRouteClose}>
      <Sidebar open={sidebarOpen} onClose={closeSidebar} />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar onMenuToggle={toggleSidebar} />
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/predictions" element={<Predictions />} />
            <Route path="/odds" element={<OddsGroups />} />
            <Route path="/results" element={<Results />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/subscribe" element={<Subscribe />} />
            <Route path="/account" element={<Account />} />
            <Route path="/admin" element={<Admin />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}
