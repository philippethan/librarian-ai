import { useCallback, useEffect, useMemo, useState } from 'react';
import { getCover, listBooks, scanBooks } from '../../api/books';
import { getStats } from '../../api/stats';
import DetailPanel from '../detail/DetailPanel';
import BookCard from './BookCard';
import FilterBar from './FilterBar';
import './BookList.css';

const POLL_INTERVAL = 5000;

function ConfidenceBar({ score }) {
  const pct = Math.round((score ?? 0) * 100);
  let fillClass = 'conf-bar__fill--low';
  if (score >= 0.7) fillClass = 'conf-bar__fill--high';
  else if (score >= 0.4) fillClass = 'conf-bar__fill--mid';
  return (
    <div className="conf-bar" title={`Confidence: ${pct}%`}>
      <div className={`conf-bar__fill ${fillClass}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function StatusBadge({ status }) {
  return <span className={`badge badge--${status}`}>{status}</span>;
}

const EMPTY_FILTERS = {
  search: '',
  status: '',
  extraction_method: '',
  reading_status: '',
  shelf_id: '',
};

function nextDir(current, col, sortState) {
  if (sortState.col !== col) return 'asc';
  if (sortState.dir === 'asc') return 'desc';
  return null; // null = clear sort
}

function SortIcon({ col, sortState }) {
  if (sortState.col !== col) return <span className="sort-icon sort-icon--idle">↕</span>;
  return <span className="sort-icon sort-icon--active">{sortState.dir === 'asc' ? '↑' : '↓'}</span>;
}

export default function BookList() {
  const [books, setBooks] = useState([]);
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [colFilters, setColFilters] = useState({ title: '', author: '' });
  const [sort, setSort] = useState({ col: null, dir: 'asc' });
  const [viewMode, setViewMode] = useState('table');
  const [selectedBook, setSelectedBook] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanPath, setScanPath] = useState('');
  const [scanLoading, setScanLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [scanError, setScanError] = useState('');
  const [scanResult, setScanResult] = useState(null);

  const displayedBooks = useMemo(() => {
    let list = books;

    // Column-level text filters (client-side)
    if (colFilters.title.trim()) {
      const q = colFilters.title.trim().toLowerCase();
      list = list.filter(b => (b.title ?? b.filename ?? '').toLowerCase().includes(q));
    }
    if (colFilters.author.trim()) {
      const q = colFilters.author.trim().toLowerCase();
      list = list.filter(b => (b.author ?? '').toLowerCase().includes(q));
    }

    // Sort
    if (sort.col) {
      list = [...list].sort((a, b) => {
        const av = a[sort.col] ?? '';
        const bv = b[sort.col] ?? '';
        const cmp = typeof av === 'number' && typeof bv === 'number'
          ? av - bv
          : String(av).localeCompare(String(bv));
        return sort.dir === 'asc' ? cmp : -cmp;
      });
    }
    return list;
  }, [books, colFilters, sort]);

  function handleSort(col) {
    setSort(prev => {
      const dir = nextDir(null, col, prev);
      return dir ? { col, dir } : { col: null, dir: 'asc' };
    });
  }

  const fetchBooks = useCallback(() => {
    const params = {};
    if (filters.search) params.search = filters.search;
    if (filters.status) params.status = filters.status;
    if (filters.extraction_method) params.extraction_method = filters.extraction_method;
    if (filters.reading_status) params.reading_status = filters.reading_status;
    if (filters.shelf_id) params.shelf_id = filters.shelf_id;
    setLoading(true);
    listBooks(params)
      .then(r => setBooks(r.data.books ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [filters]);

  const fetchStats = useCallback(() => {
    getStats().then(r => setStats(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    fetchBooks();
  }, [fetchBooks]);

  useEffect(() => {
    fetchStats();
    const id = setInterval(fetchStats, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchStats]);

  async function handleScan() {
    if (!scanPath.trim()) return;
    setScanLoading(true);
    setScanError('');
    setScanResult(null);
    try {
      const res = await scanBooks({ path: scanPath.trim() });
      setScanResult(res.data);
      fetchBooks();
      fetchStats();
    } catch (err) {
      setScanError(err?.response?.data?.detail ?? 'Scan failed.');
    } finally {
      setScanLoading(false);
    }
  }

  function closeScan() {
    setScanOpen(false);
    setScanPath('');
    setScanError('');
    setScanResult(null);
  }

  return (
    <div className="book-list-page">
      {stats && (
        <div className="stats-bar">
          <span className="stats-bar__item">
            Total <strong>{stats.total_books}</strong>
          </span>
          <span className="stats-bar__item stats-bar__item--done">
            Done <strong>{stats.done}</strong>
          </span>
          <span className="stats-bar__item stats-bar__item--partial">
            Partial <strong>{stats.partial}</strong>
          </span>
          <span className="stats-bar__item stats-bar__item--error">
            Error <strong>{stats.error}</strong>
          </span>
          {stats.pending > 0 && (
            <span className="stats-bar__item stats-bar__item--pending">
              Pending <strong>{stats.pending}</strong>
            </span>
          )}
          {stats.processing > 0 && (
            <span className="stats-bar__item stats-bar__item--processing">
              Processing <strong>{stats.processing}</strong>
            </span>
          )}
        </div>
      )}

      <FilterBar
        filters={filters}
        onFiltersChange={setFilters}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onScan={() => setScanOpen(true)}
      />

      {loading && <div className="book-list-page__loading">Loading…</div>}

      {!loading && viewMode === 'table' && (
        <div className="book-table-wrap">
          <table className="book-table">
            <thead>
              <tr>
                <th className="book-table__th book-table__th--cover"></th>
                <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('title')}>
                  Title <SortIcon col="title" sortState={sort} />
                </th>
                <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('author')}>
                  Author <SortIcon col="author" sortState={sort} />
                </th>
                <th className="book-table__th book-table__th--conf book-table__th--sortable" onClick={() => handleSort('confidence_score')}>
                  Confidence <SortIcon col="confidence_score" sortState={sort} />
                </th>
                <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('status')}>
                  Status <SortIcon col="status" sortState={sort} />
                </th>
                <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('extraction_method')}>
                  Method <SortIcon col="extraction_method" sortState={sort} />
                </th>
                <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('reading_status')}>
                  Reading <SortIcon col="reading_status" sortState={sort} />
                </th>
                <th className="book-table__th"></th>
              </tr>
              <tr className="book-table__filter-row">
                <td></td>
                <td>
                  <input
                    className="book-table__col-filter"
                    placeholder="Filter title…"
                    value={colFilters.title}
                    onChange={e => setColFilters(f => ({ ...f, title: e.target.value }))}
                    onClick={e => e.stopPropagation()}
                  />
                </td>
                <td>
                  <input
                    className="book-table__col-filter"
                    placeholder="Filter author…"
                    value={colFilters.author}
                    onChange={e => setColFilters(f => ({ ...f, author: e.target.value }))}
                    onClick={e => e.stopPropagation()}
                  />
                </td>
                <td colSpan={5}></td>
              </tr>
            </thead>
            <tbody>
              {displayedBooks.map(book => (
                <tr
                  key={book.id}
                  className="book-table__row"
                  onClick={() => setSelectedBook(book)}
                  tabIndex={0}
                  onKeyDown={e => e.key === 'Enter' && setSelectedBook(book)}
                >
                  <td className="book-table__cell book-table__cell--cover">
                    <img
                      src={getCover(book.id)}
                      alt=""
                      className="book-table__thumb"
                      onError={e => { e.currentTarget.style.display = 'none'; }}
                    />
                  </td>
                  <td className="book-table__cell book-table__cell--title">
                    {book.title ?? book.filename}
                  </td>
                  <td className="book-table__cell book-table__cell--author">
                    {book.author ?? '—'}
                  </td>
                  <td className="book-table__cell book-table__cell--conf">
                    <ConfidenceBar score={book.confidence_score ?? 0} />
                  </td>
                  <td className="book-table__cell">
                    <StatusBadge status={book.status} />
                  </td>
                  <td className="book-table__cell">
                    {book.extraction_method && (
                      <span className="chip">{book.extraction_method}</span>
                    )}
                  </td>
                  <td className="book-table__cell">
                    {book.reading_status && (
                      <span className={`badge badge--reading-${book.reading_status}`}>
                        {book.reading_status}
                      </span>
                    )}
                  </td>
                  <td className="book-table__cell">
                    {book.duplicate_of && (
                      <span className="badge badge--dupe">DUPE</span>
                    )}
                  </td>
                </tr>
              ))}
              {displayedBooks.length === 0 && (
                <tr>
                  <td colSpan={8} className="book-table__empty">No books found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {!loading && viewMode === 'card' && (
        <div className="book-grid">
          {books.map(book => (
            <BookCard key={book.id} book={book} onClick={setSelectedBook} />
          ))}
          {books.length === 0 && (
            <div className="book-grid__empty">No books found.</div>
          )}
        </div>
      )}

      {scanOpen && (
        <div className="modal-overlay" onClick={closeScan}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Scan folder</h2>
            {!scanResult ? (
              <>
                <input
                  className="modal__input"
                  type="text"
                  placeholder="C:/Users/Than/Books"
                  value={scanPath}
                  onChange={e => setScanPath(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleScan()}
                  autoFocus
                />
                {scanError && <p className="modal__error">{scanError}</p>}
                <div className="modal__actions">
                  <button className="btn btn--secondary" onClick={closeScan}>
                    Cancel
                  </button>
                  <button
                    className="btn btn--primary"
                    onClick={handleScan}
                    disabled={scanLoading || !scanPath.trim()}
                  >
                    {scanLoading ? 'Checking for duplicates…' : 'Scan'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="scan-result">
                  <p className="scan-result__row">
                    <strong>{scanResult.hashed}</strong> files found
                  </p>
                  {scanResult.duplicates > 0 && (
                    <p className="scan-result__row scan-result__row--warn">
                      <strong>{scanResult.duplicates}</strong> duplicate{scanResult.duplicates !== 1 ? 's' : ''} detected — skipped
                    </p>
                  )}
                  <p className="scan-result__row scan-result__row--ok">
                    <strong>{scanResult.queued}</strong> book{scanResult.queued !== 1 ? 's' : ''} queued for extraction
                  </p>
                </div>
                <div className="modal__actions">
                  <button className="btn btn--primary" onClick={closeScan}>
                    Close
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {selectedBook && (
        <DetailPanel
          book={selectedBook}
          onClose={() => setSelectedBook(null)}
          onBookUpdated={updatedBook => {
            setBooks(prev => prev.map(b => b.id === updatedBook.id ? updatedBook : b));
            setSelectedBook(updatedBook);
          }}
        />
      )}
    </div>
  );
}
