import { BrowserRouter, Routes, Route } from 'react-router-dom';
import TopBar from './components/layout/TopBar';
import Sidebar from './components/layout/Sidebar';
import BookList from './pages/BookList';
import ShelvesPage from './pages/ShelvesPage';
import ShelfDetail from './pages/ShelfDetail';
import AnalyticsPage from './pages/AnalyticsPage';
import DuplicatesPage from './pages/DuplicatesPage';
import SettingsPage from './pages/SettingsPage';
import './theme.css';
import './App.css';

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <TopBar />
        <div className="app-body">
          <Sidebar />
          <main className="app-main">
            <Routes>
              <Route path="/" element={<BookList />} />
              <Route path="/shelves" element={<ShelvesPage />} />
              <Route path="/shelves/:id" element={<ShelfDetail />} />
              <Route path="/analytics" element={<AnalyticsPage />} />
              <Route path="/duplicates" element={<DuplicatesPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  );
}
