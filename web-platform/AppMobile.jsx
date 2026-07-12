import React, { useState, useEffect } from 'react';
import { Home, TrendingUp, Shield, Briefcase, Settings as SettingsIcon } from 'lucide-react';
import DashboardMobile from './pages/DashboardMobile';
import ProfitLossTrackerMobile from './pages/ProfitLossTrackerMobile';
import StopLossTracker from './pages/StopLossTracker';
import PortfolioMobile from './pages/PortfolioMobile';
import Settings from './pages/Settings';
import { ThemeToggle } from './hooks/useTheme';

export default function AppMobile() {
  const [currentPage, setCurrentPage] = useState('dashboard');
  // Increments on every nav tap so the page remounts and re-fetches fresh data
  const [navTick, setNavTick] = useState(0);

  const goto = (id) => {
    setCurrentPage(id);
    setNavTick(t => t + 1);
  };

  const navigation = [
    { id: 'dashboard', name: 'Home', icon: Home },
    { id: 'pl-tracker', name: 'P&L', icon: TrendingUp },
    { id: 'sl-tracker', name: 'SL', icon: Shield },
    { id: 'portfolio', name: 'Portfolio', icon: Briefcase },
    { id: 'settings', name: 'Settings', icon: SettingsIcon },
  ];

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <DashboardMobile />;
      case 'pl-tracker':
        return <ProfitLossTrackerMobile />;
      case 'sl-tracker':
        return <StopLossTracker />;
      case 'portfolio':
        return <PortfolioMobile />;
      case 'settings':
        return <Settings />;
      default:
        return <DashboardMobile />;
    }
  };

  return (
    <div className="h-screen bg-slate-900 flex flex-col">
      {/* Page Title Bar */}
      <div className="bg-slate-800 border-b border-slate-700 px-4 py-3 sticky top-0 z-20 flex items-center justify-between">
        <h1 className="text-lg font-bold text-white">
          {navigation.find(n => n.id === currentPage)?.name || 'Dashboard'}
        </h1>
        <ThemeToggle />
      </div>

      {/* Content Area — key forces remount (fresh fetch) on every nav tap */}
      <div className="flex-1 overflow-y-auto pb-20" key={`${currentPage}-${navTick}`}>
        {renderPage()}
      </div>

      {/* Bottom Navigation */}
      <div className="fixed bottom-0 left-0 right-0 bg-slate-800 border-t border-slate-700 px-0 py-2 flex justify-around items-center">
        {navigation.map(item => {
          const Icon = item.icon;
          const isActive = currentPage === item.id;
          return (
            <button
              key={item.id}
              onClick={() => goto(item.id)}
              className={`flex flex-col items-center py-2 px-3 rounded-lg transition-all flex-1 ${
                isActive
                  ? 'text-blue-400'
                  : 'text-slate-400'
              }`}
            >
              <Icon className="w-6 h-6 mb-1" />
              <span className="text-xs font-semibold">{item.name}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
