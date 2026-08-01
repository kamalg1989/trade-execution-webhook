import React, { useState, useEffect } from 'react';
import { Home, Shield, Briefcase, LogOut, Menu, X, Settings as SettingsIcon } from 'lucide-react';
import { useDevice } from './hooks/useDevice';
import { ThemeToggle } from './hooks/useTheme';
import Dashboard from './pages/Dashboard';
import StopLossTracker from './pages/StopLossTracker';
import Portfolio from './pages/Portfolio';
import Settings from './pages/Settings';
import AppMobile from './AppMobile';

export default function App() {
  const { isMobile, isReady } = useDevice();
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [user, setUser] = useState(null);
  // Increments on every nav click so the active page remounts and re-fetches
  const [navTick, setNavTick] = useState(0);
  const goto = (id) => {
    setCurrentPage(id);
    setNavTick(t => t + 1);
    setSidebarOpen(false);
  };

  // NOTE: All hooks MUST be called before any conditional returns (Rules of Hooks)
  // fetch logic is inlined so the mobile early-return can never break it (TDZ safety)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch('/api/user');
        if (!response.ok) return;
        const data = await response.json();
        if (!cancelled) setUser(data);
      } catch (error) {
        console.error('Failed to fetch user:', error);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Use mobile app if screen width < 768px
  if (isMobile) {
    return <AppMobile />;
  }

  const handleLogout = () => {
    if (window.confirm('Logout from your account?')) {
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
  };

  const navigation = [
    { id: 'dashboard', name: 'Dashboard', icon: Home, description: 'Daily recommendations' },
    { id: 'sl-tracker', name: 'Today', icon: Shield, description: 'Daily actions & P&L' },
    { id: 'portfolio', name: 'Portfolio', icon: Briefcase, description: 'Holdings & history' },
    { id: 'settings', name: 'Settings', icon: SettingsIcon, description: 'Screener configuration' },
  ];

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <Dashboard />;
      case 'sl-tracker':
        return <StopLossTracker />;
      case 'portfolio':
        return <Portfolio />;
      case 'settings':
        return <Settings />;
      default:
        return <Dashboard />;
    }
  };

  return (
    <div className="flex h-screen bg-slate-900">
      {/* Sidebar - Fixed positioning on mobile */}
      <div
        className={`${
          sidebarOpen ? 'w-64' : 'w-0'
        } fixed lg:static lg:w-64 h-full bg-slate-800 text-white transition-all duration-300 overflow-hidden flex flex-col border-r border-slate-700 z-40`}
      >
        {/* Logo */}
        <div className="p-6 border-b border-slate-700">
          <h2 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
            TradeHub
          </h2>
          <p className="text-xs text-slate-400 mt-1">Smart Trading Platform</p>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-4 space-y-2 overflow-y-auto">
          {navigation.map(item => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                onClick={() => goto(item.id)}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-all ${
                  currentPage === item.id
                    ? 'bg-blue-600 text-white shadow-lg'
                    : 'text-slate-300 hover:bg-slate-700'
                }`}
              >
                <Icon className="w-5 h-5" />
                <div className="text-left">
                  <p className="font-semibold">{item.name}</p>
                  <p className="text-xs text-slate-400">{item.description}</p>
                </div>
              </button>
            );
          })}
        </nav>

        {/* User Section */}
        <div className="p-4 border-t border-slate-700 space-y-3">
          {user && (
            <div className="bg-slate-700 rounded-lg p-3">
              <p className="font-semibold text-sm">{user.name}</p>
              <p className="text-xs text-slate-400">{user.email}</p>
            </div>
          )}
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2 px-4 py-2 text-red-400 hover:bg-red-900/20 rounded-lg transition-all"
          >
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </div>

      {/* Mobile Overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 lg:hidden z-30"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Main Content */}
      <div className="flex-1 overflow-auto flex flex-col lg:ml-0">
        {/* Top Bar */}
        <div className="bg-slate-800 border-b border-slate-700 px-4 lg:px-6 py-4 flex items-center justify-between sticky top-0 z-20">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="text-slate-400 hover:text-white lg:hidden flex-shrink-0"
          >
            {sidebarOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>

          <div className="flex-1 text-center px-4">
            <h1 className="text-lg lg:text-xl font-bold text-white truncate">
              {navigation.find(n => n.id === currentPage)?.name || 'Dashboard'}
            </h1>
          </div>

          <div className="flex items-center gap-2 lg:gap-4 flex-shrink-0">
            <ThemeToggle />
            <div className="text-right text-xs lg:text-sm hidden sm:block">
              <p className="text-slate-400">Last Updated</p>
              <p className="text-white font-semibold">
                {new Date().toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit'
                })}
              </p>
            </div>
          </div>
        </div>

        {/* Page Content — key forces remount (fresh fetch) on every nav click */}
        <div className="flex-1 overflow-auto" key={`${currentPage}-${navTick}`}>
          {renderPage()}
        </div>
      </div>
    </div>
  );
}
