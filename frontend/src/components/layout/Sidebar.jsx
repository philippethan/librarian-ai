import { NavLink } from 'react-router-dom';
import './Sidebar.css';

const links = [
  { to: '/', label: 'Books' },
  { to: '/shelves', label: 'Shelves' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/duplicates', label: 'Duplicates' },
  { to: '/settings', label: 'Settings' },
];

export default function Sidebar() {
  return (
    <nav className="sidebar">
      {links.map(({ to, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) => 'sidebar-link' + (isActive ? ' active' : '')}
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
