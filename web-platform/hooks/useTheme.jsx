import React, { useState, useEffect } from 'react';
import { Sun, Moon } from 'lucide-react';

// Apply saved theme immediately on module load (before first paint)
const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('theme') : null;
if (saved === 'light') document.documentElement.classList.add('light');

export function useTheme() {
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'dark');

  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light');
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggle = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'));
  return { theme, toggle };
}

export function ThemeToggle({ className = '' }) {
  const { theme, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className={`p-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 ${className}`}
    >
      {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  );
}
