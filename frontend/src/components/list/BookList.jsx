import { useCallback, useEffect, useState } from 'react';
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

export default function BookList() {
  const [books, setBooks] = useState([]);
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [viewMode, setViewMode] = useState('table');
  const [selectedBook, setSelectedBook] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanPath, setScanPath] = useState('');
  const [scanLoading, setScanLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [scanError, setScanError] = useState('');
  const [scanResult, setScanResult] = useState(null);

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
                <th className="book-table__th">Title</th>
                <th className="book-table__th">Author</th>
                <th className="book-table__th book-table__th--conf">Confidence</th>
                <th className="book-table__th">Status</th>
                <th className="book-table__th">Method</th>
                <th className="book-table__th">Reading</th>
                <th className="book-table__th"></th>
              </tr>
            </thead>
            <tbody>
              {books.map(book => (
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
              {books.length === 0 && (
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
