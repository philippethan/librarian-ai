import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cleanWatermarks, getCover, listBooks, scanBooks } from '../../api/books';
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

// ── Excel-style column filter popup ──────────────────────────────────────────

function ColFilter({ filter, onFilterChange, type, options = [] }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btnRef = useRef(null);
  const popupRef = useRef(null);
  const active = Boolean(filter);

  function handleToggle(e) {
    e.stopPropagation();
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setPos({ top: r.bottom + 4, left: r.left });
    }
    setOpen(o => !o);
  }

  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (popupRef.current?.contains(e.target)) return;
      if (btnRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        className={`col-filter__btn${active ? ' col-filter__btn--active' : ''}`}
        onClick={handleToggle}
        title={active ? `Filter active: ${filter}` : 'Filter'}
      >▼</button>
      {open && createPortal(
        <div
          ref={popupRef}
          className="col-filter__popup"
          style={{ position: 'fixed', top: pos.top, left: pos.left }}
          onClick={e => e.stopPropagation()}
        >
          {type === 'text' ? (
            <>
              <input
                className="col-filter__input"
                placeholder="Filter…"
                value={filter}
                onChange={e => onFilterChange(e.target.value)}
                autoFocus
              />
              {active && (
                <button className="col-filter__clear" onClick={() => { onFilterChange(''); setOpen(false); }}>
                  Clear filter
                </button>
              )}
            </>
          ) : (
            <ul className="col-filter__list">
              <li
                className={`col-filter__option${!active ? ' col-filter__option--selected' : ''}`}
                onClick={() => { onFilterChange(''); setOpen(false); }}
              >
                (All)
              </li>
              {options.map(opt => (
                <li
                  key={opt.value}
                  className={`col-filter__option${filter === opt.value ? ' col-filter__option--selected' : ''}`}
                  onClick={() => { onFilterChange(active && filter === opt.value ? '' : opt.value); setOpen(false); }}
                >
                  {opt.label}
                </li>
              ))}
            </ul>
          )}
        </div>,
        document.body
      )}
    </>
  );
}

const EMPTY_FILTERS = {
  search: '',
  status: '',
  extraction_method: '',
  reading_status: '',
  shelf_id: '',
};

const TABLE_COLS = [
  { key: 'title',             label: 'Title' },
  { key: 'author',            label: 'Author' },
  { key: 'confidence',        label: 'Confidence' },
  { key: 'status',            label: 'Status' },
  { key: 'extraction_method', label: 'Method' },
  { key: 'reading_status',    label: 'Reading' },
  { key: 'dupe',              label: 'Dupe flag' },
];

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
  const [colFilters, setColFilters] = useState({
    title: '', author: '', confidence: '', status: '', extraction_method: '', reading_status: '',
  });
  const [sort, setSort] = useState({ col: null, dir: 'asc' });
  const [viewMode, setViewMode] = useState('table');
  const [selectedBook, setSelectedBook] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanPath, setScanPath] = useState('');
  const [scanLoading, setScanLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [scanError, setScanError] = useState('');
  const [scanResult, setScanResult] = useState(null);
  const [cleanOpen, setCleanOpen] = useState(false);
  const [cleanLoading, setCleanLoading] = useState(false);
  const [cleanPreview, setCleanPreview] = useState(null);  // {changes, total}
  const [cleanResult, setCleanResult] = useState(null);    // {renamed, errors}
  const [cleanError, setCleanError] = useState('');
  const [visibleCols, setVisibleCols] = useState(() => new Set(TABLE_COLS.map(c => c.key)));

  function toggleCol(key) {
    setVisibleCols(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size > 1) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  const displayedBooks = useMemo(() => {
    let list = books;

    if (colFilters.title.trim()) {
      const q = colFilters.title.trim().toLowerCase();
      list = list.filter(b => (b.title ?? b.filename ?? '').toLowerCase().includes(q));
    }
    if (colFilters.author.trim()) {
      const q = colFilters.author.trim().toLowerCase();
      list = list.filter(b => (b.author ?? '').toLowerCase().includes(q));
    }
    if (colFilters.confidence) {
      list = list.filter(b => {
        const s = b.confidence_score ?? 0;
        if (colFilters.confidence === 'high') return s >= 0.7;
        if (colFilters.confidence === 'mid')  return s >= 0.4 && s < 0.7;
        if (colFilters.confidence === 'low')  return s < 0.4;
        return true;
      });
    }
    if (colFilters.status)
      list = list.filter(b => b.status === colFilters.status);
    if (colFilters.extraction_method)
      list = list.filter(b => b.extraction_method === colFilters.extraction_method);
    if (colFilters.reading_status)
      list = list.filter(b => (b.reading_status ?? '') === colFilters.reading_status);

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

  const colOptions = useMemo(() => {
    const uniq = (field) => [...new Set(books.map(b => b[field]).filter(Boolean))].sort();
    const label = v => v.charAt(0).toUpperCase() + v.slice(1).replace(/_/g, ' ');
    return {
      status:            uniq('status').map(v => ({ value: v, label: label(v) })),
      extraction_method: uniq('extraction_method').map(v => ({ value: v, label: v })),
      reading_status:    uniq('reading_status').map(v => ({ value: v, label: label(v) })),
      confidence: [
        { value: 'high', label: 'High  ≥ 70%' },
        { value: 'mid',  label: 'Mid   40–70%' },
        { value: 'low',  label: 'Low  < 40%'  },
      ],
    };
  }, [books]);

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
      .then(r => setBooks(Array.isArray(r.data) ? r.data : (r.data.books ?? [])))
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

  async function handleOpenClean() {
    setCleanOpen(true);
    setCleanPreview(null);
    setCleanResult(null);
    setCleanError('');
    setCleanLoading(true);
    try {
      const res = await cleanWatermarks(true);
      setCleanPreview(res.data);
    } catch (err) {
      setCleanError(err?.response?.data?.detail ?? 'Preview failed.');
    } finally {
      setCleanLoading(false);
    }
  }

  async function handleConfirmClean() {
    setCleanLoading(true);
    try {
      const res = await cleanWatermarks(false);
      setCleanResult(res.data);
      setCleanPreview(null);
      fetchBooks();
    } catch (err) {
      setCleanError(err?.response?.data?.detail ?? 'Clean failed.');
    } finally {
      setCleanLoading(false);
    }
  }

  function closeClean() {
    setCleanOpen(false);
    setCleanPreview(null);
    setCleanResult(null);
    setCleanError('');
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
        onClean={handleOpenClean}
        allCols={TABLE_COLS}
        visibleCols={visibleCols}
        onToggleCol={toggleCol}
      />

      {loading && <div className="book-list-page__loading">Loading…</div>}

      {!loading && viewMode === 'table' && (
        <div className="book-table-wrap">
          <table className="book-table">
            <thead>
              <tr>
                <th className="book-table__th book-table__th--cover"></th>
                {visibleCols.has('title') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('title')}>
                    <span className="book-table__th-inner">
                      Title <SortIcon col="title" sortState={sort} />
                      <ColFilter filter={colFilters.title} onFilterChange={v => setColFilters(f => ({ ...f, title: v }))} type="text" />
                    </span>
                  </th>
                )}
                {visibleCols.has('author') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('author')}>
                    <span className="book-table__th-inner">
                      Author <SortIcon col="author" sortState={sort} />
                      <ColFilter filter={colFilters.author} onFilterChange={v => setColFilters(f => ({ ...f, author: v }))} type="text" />
                    </span>
                  </th>
                )}
                {visibleCols.has('confidence') && (
                  <th className="book-table__th book-table__th--conf book-table__th--sortable" onClick={() => handleSort('confidence_score')}>
                    <span className="book-table__th-inner">
                      Confidence <SortIcon col="confidence_score" sortState={sort} />
                      <ColFilter filter={colFilters.confidence} onFilterChange={v => setColFilters(f => ({ ...f, confidence: v }))} type="select" options={colOptions.confidence} />
                    </span>
                  </th>
                )}
                {visibleCols.has('status') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('status')}>
                    <span className="book-table__th-inner">
                      Status <SortIcon col="status" sortState={sort} />
                      <ColFilter filter={colFilters.status} onFilterChange={v => setColFilters(f => ({ ...f, status: v }))} type="select" options={colOptions.status} />
                    </span>
                  </th>
                )}
                {visibleCols.has('extraction_method') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('extraction_method')}>
                    <span className="book-table__th-inner">
                      Method <SortIcon col="extraction_method" sortState={sort} />
                      <ColFilter filter={colFilters.extraction_method} onFilterChange={v => setColFilters(f => ({ ...f, extraction_method: v }))} type="select" options={colOptions.extraction_method} />
                    </span>
                  </th>
                )}
                {visibleCols.has('reading_status') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('reading_status')}>
                    <span className="book-table__th-inner">
                      Reading <SortIcon col="reading_status" sortState={sort} />
                      <ColFilter filter={colFilters.reading_status} onFilterChange={v => setColFilters(f => ({ ...f, reading_status: v }))} type="select" options={colOptions.reading_status} />
                    </span>
                  </th>
                )}
                {visibleCols.has('dupe') && <th className="book-table__th"></th>}
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
                  {visibleCols.has('title') && (
                    <td className="book-table__cell book-table__cell--title">
                      {book.title ?? book.filename}
                    </td>
                  )}
                  {visibleCols.has('author') && (
                    <td className="book-table__cell book-table__cell--author">
                      {book.author ?? '—'}
                    </td>
                  )}
                  {visibleCols.has('confidence') && (
                    <td className="book-table__cell book-table__cell--conf">
                      <ConfidenceBar score={book.confidence_score ?? 0} />
                    </td>
                  )}
                  {visibleCols.has('status') && (
                    <td className="book-table__cell">
                      <StatusBadge status={book.status} />
                    </td>
                  )}
                  {visibleCols.has('extraction_method') && (
                    <td className="book-table__cell">
                      {book.extraction_method && (
                        <span className="chip">{book.extraction_method}</span>
                      )}
                    </td>
                  )}
                  {visibleCols.has('reading_status') && (
                    <td className="book-table__cell">
                      {book.reading_status && (
                        <span className={`badge badge--reading-${book.reading_status}`}>
                          {book.reading_status}
                        </span>
                      )}
                    </td>
                  )}
                  {visibleCols.has('dupe') && (
                    <td className="book-table__cell">
                      {book.duplicate_of && (
                        <span className="badge badge--dupe">DUPE</span>
                      )}
                    </td>
                  )}
                </tr>
              ))}
              {displayedBooks.length === 0 && (
                <tr>
                  <td colSpan={1 + visibleCols.size} className="book-table__empty">No books found.</td>
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
                  <p className="scan-result__row scan-result__row--ok">
                    <strong>{scanResult.added ?? 0}</strong> new book{(scanResult.added ?? 0) !== 1 ? 's' : ''} queued for extraction
                  </p>
                  {(scanResult.skipped ?? 0) > 0 && (
                    <p className="scan-result__row">
                      <strong>{scanResult.skipped}</strong> already in library — skipped
                    </p>
                  )}
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

      {cleanOpen && (
        <div className="modal-overlay" onClick={closeClean}>
          <div className="modal modal--wide" onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Clean watermark filenames</h2>

            {cleanLoading && <p className="modal__hint">Scanning filenames…</p>}
            {cleanError  && <p className="modal__error">{cleanError}</p>}

            {cleanResult && (
              <div className="scan-result">
                <p className="scan-result__row scan-result__row--ok">
                  <strong>{cleanResult.renamed}</strong> file{cleanResult.renamed !== 1 ? 's' : ''} renamed successfully.
                </p>
                {cleanResult.errors?.length > 0 && (
                  <p className="scan-result__row scan-result__row--warn">
                    <strong>{cleanResult.errors.length}</strong> error{cleanResult.errors.length !== 1 ? 's' : ''} — check the console.
                  </p>
                )}
              </div>
            )}

            {cleanPreview && !cleanResult && (
              <>
                {cleanPreview.total === 0 ? (
                  <p className="modal__hint">No watermark strings found in any filename — nothing to do.</p>
                ) : (
                  <>
                    <p className="modal__hint">
                      <strong>{cleanPreview.total}</strong> file{cleanPreview.total !== 1 ? 's' : ''} will be renamed:
                    </p>
                    <div className="clean-preview-list">
                      {cleanPreview.changes.map(c => (
                        <div key={c.id} className="clean-preview-row">
                          <span className="clean-preview-row__old">{c.old_filename}</span>
                          <span className="clean-preview-row__arrow">→</span>
                          <span className="clean-preview-row__new">{c.new_filename}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}

            <div className="modal__actions">
              <button className="btn btn--secondary" onClick={closeClean}>
                {cleanResult ? 'Close' : 'Cancel'}
              </button>
              {cleanPreview && !cleanResult && cleanPreview.total > 0 && (
                <button className="btn btn--primary" onClick={handleConfirmClean} disabled={cleanLoading}>
                  {cleanLoading ? 'Renaming…' : `Rename ${cleanPreview.total} file${cleanPreview.total !== 1 ? 's' : ''}`}
                </button>
              )}
            </div>
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
