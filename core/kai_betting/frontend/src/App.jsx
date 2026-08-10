import { Routes, Route } from 'react-router-dom';
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

export default function App() {
  return (
    <AuthProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <TopBar />
          <main className="flex-1 overflow-y-auto p-6">
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
    </AuthProvider>
  );
}
