import { useState, useEffect } from 'react';
import './TopBar.css';

function ThemeToggle() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem('librarian_theme') ?? 'light'
  );

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('librarian_theme', theme);
  }, [theme]);

  return (
    <button
      className="theme-toggle"
      onClick={() => setTheme(t => t === 'light' ? 'dark' : 'light')}
      aria-label="Toggle theme"
    >
      {theme === 'light' ? '🌙' : '☀️'}
    </button>
  );
}

export default function TopBar() {
  return (
    <header className="topbar">
      <span className="topbar-title">LibrarianAI</span>
      <ThemeToggle />
    </header>
  );
}
