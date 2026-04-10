import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { bulkCategorize, cleanWatermarks, deleteBatch, getCover, listBooks, scanBooks } from '../../api/books';
import { getStats } from '../../api/stats';
import DetailPanel from '../detail/DetailPanel';
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
  { key: 'year',              label: 'Year' },
  { key: 'language',          label: 'Language' },
  { key: 'category',          label: 'Category' },
  { key: 'subcategory',       label: 'Subcategory' },
  { key: 'difficulty',        label: 'Difficulty' },
  { key: 'file_type',         label: 'Type' },
  { key: 'file_size',         label: 'Size' },
  { key: 'confidence',        label: 'Confidence' },
  { key: 'status',            label: 'Status' },
  { key: 'extraction_method', label: 'Method' },
  { key: 'reading_status',    label: 'Reading' },
  { key: 'added_at',          label: 'Added' },
  { key: 'processed_at',      label: 'Processed' },
  { key: 'dupe',              label: 'Dupe' },
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
    title: '', author: '', year: '', language: '', category: '', subcategory: '',
    difficulty: '', file_type: '', confidence: '', status: '', extraction_method: '', reading_status: '',
  });
  const [sort, setSort] = useState({ col: null, dir: 'asc' });
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
  const [visibleCols, setVisibleCols] = useState(() => new Set([
    'title', 'author', 'year', 'category', 'subcategory', 'reading_status', 'status',
  ]));
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleteFile, setBulkDeleteFile] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkCategorizing, setBulkCategorizing] = useState(false);

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

  function toggleSelect(id) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    const displayedIdSet = new Set(displayedBooks.map(b => b.id));
    const allSelected = displayedBooks.every(b => selectedIds.has(b.id));
    if (allSelected) {
      setSelectedIds(prev => { const next = new Set(prev); displayedIdSet.forEach(id => next.delete(id)); return next; });
    } else {
      setSelectedIds(prev => { const next = new Set(prev); displayedIdSet.forEach(id => next.add(id)); return next; });
    }
  }

  async function handleBulkDelete() {
    setBulkDeleting(true);
    try {
      await deleteBatch([...selectedIds], bulkDeleteFile);
      setBooks(prev => prev.filter(b => !selectedIds.has(b.id)));
      setSelectedIds(new Set());
      setBulkDeleteOpen(false);
    } catch {}
    finally { setBulkDeleting(false); }
  }

  async function handleBulkCategorize() {
    setBulkCategorizing(true);
    try {
      await bulkCategorize([...selectedIds]);
      // Categories will appear progressively as the LLM finishes each book.
      // Start polling so the table updates automatically.
      fetchBooks();
    } catch {}
    finally { setBulkCategorizing(false); }
  }

  const displayedBooks = useMemo(() => {
    let list = books;

    const textFilter = (field, getter) => {
      if (!colFilters[field]?.trim()) return;
      const q = colFilters[field].trim().toLowerCase();
      list = list.filter(b => (getter(b) ?? '').toLowerCase().includes(q));
    };
    textFilter('title',  b => b.title ?? b.filename);
    textFilter('author', b => b.author);
    textFilter('year',   b => b.year);

    if (colFilters.language)    list = list.filter(b => b.language === colFilters.language);
    if (colFilters.category)    list = list.filter(b => b.category === colFilters.category);
    if (colFilters.subcategory) list = list.filter(b => b.subcategory === colFilters.subcategory);
    if (colFilters.difficulty)  list = list.filter(b => b.difficulty === colFilters.difficulty);
    if (colFilters.file_type)   list = list.filter(b => b.file_type === colFilters.file_type);
    if (colFilters.confidence) {
      list = list.filter(b => {
        const s = b.confidence_score ?? 0;
        if (colFilters.confidence === 'high') return s >= 0.7;
        if (colFilters.confidence === 'mid')  return s >= 0.4 && s < 0.7;
        if (colFilters.confidence === 'low')  return s < 0.4;
        return true;
      });
    }
    if (colFilters.status)            list = list.filter(b => b.status === colFilters.status);
    if (colFilters.extraction_method) list = list.filter(b => b.extraction_method === colFilters.extraction_method);
    if (colFilters.reading_status)    list = list.filter(b => (b.reading_status ?? '') === colFilters.reading_status);

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
    const toOpts = (field) => uniq(field).map(v => ({ value: v, label: label(v) }));
    return {
      language:          toOpts('language'),
      category:          toOpts('category'),
      subcategory:       toOpts('subcategory'),
      difficulty:        toOpts('difficulty'),
      file_type:         toOpts('file_type'),
      status:            toOpts('status'),
      extraction_method: uniq('extraction_method').map(v => ({ value: v, label: v })),
      reading_status:    toOpts('reading_status'),
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
        onScan={() => setScanOpen(true)}
        onClean={handleOpenClean}
        allCols={TABLE_COLS}
        visibleCols={visibleCols}
        onToggleCol={toggleCol}
      />

      {selectedIds.size > 0 && (
        <div className="bulk-bar">
          <span className="bulk-bar__count">{selectedIds.size} selected</span>
          <button
            className="btn btn--sm btn--primary"
            onClick={handleBulkCategorize}
            disabled={bulkCategorizing}
          >
            {bulkCategorizing ? 'Queuing…' : 'Categorize selected'}
          </button>
          <button
            className="btn btn--sm btn--danger"
            onClick={() => { setBulkDeleteFile(false); setBulkDeleteOpen(true); }}
          >
            Delete selected
          </button>
          <button className="btn btn--sm btn--ghost" onClick={() => setSelectedIds(new Set())}>
            Clear selection
          </button>
        </div>
      )}

      {loading && <div className="book-list-page__loading">Loading…</div>}

      {!loading && (
        <div className="book-table-wrap">
          <table className="book-table">
            <thead>
              <tr>
                <th className="book-table__th book-table__th--check">
                  <input
                    type="checkbox"
                    className="book-table__check"
                    checked={displayedBooks.length > 0 && displayedBooks.every(b => selectedIds.has(b.id))}
                    ref={el => { if (el) el.indeterminate = displayedBooks.some(b => selectedIds.has(b.id)) && !displayedBooks.every(b => selectedIds.has(b.id)); }}
                    onChange={toggleSelectAll}
                    title="Select all"
                  />
                </th>
                <th className="book-table__th book-table__th--cover"></th>
                {visibleCols.has('title') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('title')}>
                    <span className="book-table__th-inner">Title <SortIcon col="title" sortState={sort} />
                      <ColFilter filter={colFilters.title} onFilterChange={v => setColFilters(f => ({ ...f, title: v }))} type="text" /></span>
                  </th>
                )}
                {visibleCols.has('author') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('author')}>
                    <span className="book-table__th-inner">Author <SortIcon col="author" sortState={sort} />
                      <ColFilter filter={colFilters.author} onFilterChange={v => setColFilters(f => ({ ...f, author: v }))} type="text" /></span>
                  </th>
                )}
                {visibleCols.has('year') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('year')}>
                    <span className="book-table__th-inner">Year <SortIcon col="year" sortState={sort} />
                      <ColFilter filter={colFilters.year} onFilterChange={v => setColFilters(f => ({ ...f, year: v }))} type="text" /></span>
                  </th>
                )}
                {visibleCols.has('language') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('language')}>
                    <span className="book-table__th-inner">Language <SortIcon col="language" sortState={sort} />
                      <ColFilter filter={colFilters.language} onFilterChange={v => setColFilters(f => ({ ...f, language: v }))} type="select" options={colOptions.language} /></span>
                  </th>
                )}
                {visibleCols.has('category') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('category')}>
                    <span className="book-table__th-inner">Category <SortIcon col="category" sortState={sort} />
                      <ColFilter filter={colFilters.category} onFilterChange={v => setColFilters(f => ({ ...f, category: v }))} type="select" options={colOptions.category} /></span>
                  </th>
                )}
                {visibleCols.has('subcategory') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('subcategory')}>
                    <span className="book-table__th-inner">Subcategory <SortIcon col="subcategory" sortState={sort} />
                      <ColFilter filter={colFilters.subcategory} onFilterChange={v => setColFilters(f => ({ ...f, subcategory: v }))} type="select" options={colOptions.subcategory} /></span>
                  </th>
                )}
                {visibleCols.has('difficulty') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('difficulty')}>
                    <span className="book-table__th-inner">Difficulty <SortIcon col="difficulty" sortState={sort} />
                      <ColFilter filter={colFilters.difficulty} onFilterChange={v => setColFilters(f => ({ ...f, difficulty: v }))} type="select" options={colOptions.difficulty} /></span>
                  </th>
                )}
                {visibleCols.has('file_type') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('file_type')}>
                    <span className="book-table__th-inner">Type <SortIcon col="file_type" sortState={sort} />
                      <ColFilter filter={colFilters.file_type} onFilterChange={v => setColFilters(f => ({ ...f, file_type: v }))} type="select" options={colOptions.file_type} /></span>
                  </th>
                )}
                {visibleCols.has('file_size') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('file_size')}>
                    <span className="book-table__th-inner">Size <SortIcon col="file_size" sortState={sort} /></span>
                  </th>
                )}
                {visibleCols.has('confidence') && (
                  <th className="book-table__th book-table__th--conf book-table__th--sortable" onClick={() => handleSort('confidence_score')}>
                    <span className="book-table__th-inner">Confidence <SortIcon col="confidence_score" sortState={sort} />
                      <ColFilter filter={colFilters.confidence} onFilterChange={v => setColFilters(f => ({ ...f, confidence: v }))} type="select" options={colOptions.confidence} /></span>
                  </th>
                )}
                {visibleCols.has('status') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('status')}>
                    <span className="book-table__th-inner">Status <SortIcon col="status" sortState={sort} />
                      <ColFilter filter={colFilters.status} onFilterChange={v => setColFilters(f => ({ ...f, status: v }))} type="select" options={colOptions.status} /></span>
                  </th>
                )}
                {visibleCols.has('extraction_method') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('extraction_method')}>
                    <span className="book-table__th-inner">Method <SortIcon col="extraction_method" sortState={sort} />
                      <ColFilter filter={colFilters.extraction_method} onFilterChange={v => setColFilters(f => ({ ...f, extraction_method: v }))} type="select" options={colOptions.extraction_method} /></span>
                  </th>
                )}
                {visibleCols.has('reading_status') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('reading_status')}>
                    <span className="book-table__th-inner">Reading <SortIcon col="reading_status" sortState={sort} />
                      <ColFilter filter={colFilters.reading_status} onFilterChange={v => setColFilters(f => ({ ...f, reading_status: v }))} type="select" options={colOptions.reading_status} /></span>
                  </th>
                )}
                {visibleCols.has('added_at') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('added_at')}>
                    <span className="book-table__th-inner">Added <SortIcon col="added_at" sortState={sort} /></span>
                  </th>
                )}
                {visibleCols.has('processed_at') && (
                  <th className="book-table__th book-table__th--sortable" onClick={() => handleSort('processed_at')}>
                    <span className="book-table__th-inner">Processed <SortIcon col="processed_at" sortState={sort} /></span>
                  </th>
                )}
                {visibleCols.has('dupe') && <th className="book-table__th"></th>}
              </tr>
            </thead>
            <tbody>
              {displayedBooks.map(book => (
                <tr
                  key={book.id}
                  className={`book-table__row${selectedIds.has(book.id) ? ' book-table__row--selected' : ''}`}
                  onClick={() => setSelectedBook(book)}
                  tabIndex={0}
                  onKeyDown={e => e.key === 'Enter' && setSelectedBook(book)}
                >
                  <td className="book-table__cell book-table__cell--check" onClick={e => { e.stopPropagation(); toggleSelect(book.id); }}>
                    <input
                      type="checkbox"
                      className="book-table__check"
                      checked={selectedIds.has(book.id)}
                      onChange={() => toggleSelect(book.id)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>
                  <td className="book-table__cell book-table__cell--cover">
                    <img
                      src={getCover(book.id)}
                      alt=""
                      className="book-table__thumb"
                      onError={e => { e.currentTarget.style.display = 'none'; }}
                    />
                  </td>
                  {visibleCols.has('title') && (
                    <td className="book-table__cell book-table__cell--title">{book.title ?? book.filename}</td>
                  )}
                  {visibleCols.has('author') && (
                    <td className="book-table__cell book-table__cell--author">{book.author ?? '—'}</td>
                  )}
                  {visibleCols.has('year') && (
                    <td className="book-table__cell">{book.year ?? '—'}</td>
                  )}
                  {visibleCols.has('language') && (
                    <td className="book-table__cell">{book.language ?? '—'}</td>
                  )}
                  {visibleCols.has('category') && (
                    <td className="book-table__cell">{book.category ?? '—'}</td>
                  )}
                  {visibleCols.has('subcategory') && (
                    <td className="book-table__cell">{book.subcategory ?? '—'}</td>
                  )}
                  {visibleCols.has('difficulty') && (
                    <td className="book-table__cell">{book.difficulty ?? '—'}</td>
                  )}
                  {visibleCols.has('file_type') && (
                    <td className="book-table__cell">
                      {book.file_type && <span className="chip">{book.file_type.toUpperCase()}</span>}
                    </td>
                  )}
                  {visibleCols.has('file_size') && (
                    <td className="book-table__cell">
                      {book.file_size ? `${(book.file_size / 1024 / 1024).toFixed(1)} MB` : '—'}
                    </td>
                  )}
                  {visibleCols.has('confidence') && (
                    <td className="book-table__cell book-table__cell--conf">
                      <ConfidenceBar score={book.confidence_score ?? 0} />
                    </td>
                  )}
                  {visibleCols.has('status') && (
                    <td className="book-table__cell"><StatusBadge status={book.status} /></td>
                  )}
                  {visibleCols.has('extraction_method') && (
                    <td className="book-table__cell">
                      {book.extraction_method && <span className="chip">{book.extraction_method}</span>}
                    </td>
                  )}
                  {visibleCols.has('reading_status') && (
                    <td className="book-table__cell">
                      {book.reading_status && (
                        <span className={`badge badge--reading-${book.reading_status}`}>{book.reading_status}</span>
                      )}
                    </td>
                  )}
                  {visibleCols.has('added_at') && (
                    <td className="book-table__cell book-table__cell--date">
                      {book.added_at ? book.added_at.slice(0, 10) : '—'}
                    </td>
                  )}
                  {visibleCols.has('processed_at') && (
                    <td className="book-table__cell book-table__cell--date">
                      {book.processed_at ? book.processed_at.slice(0, 10) : '—'}
                    </td>
                  )}
                  {visibleCols.has('dupe') && (
                    <td className="book-table__cell">
                      {book.duplicate_of && <span className="badge badge--dupe">DUPE</span>}
                    </td>
                  )}
                </tr>
              ))}
              {displayedBooks.length === 0 && (
                <tr>
                  <td colSpan={2 + visibleCols.size} className="book-table__empty">No books found.</td>
                </tr>
              )}
            </tbody>
          </table>
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

      {bulkDeleteOpen && (
        <div className="modal-overlay" onClick={() => setBulkDeleteOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Delete {selectedIds.size} book{selectedIds.size !== 1 ? 's' : ''}?</h2>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem' }}>
              <input
                type="checkbox"
                checked={bulkDeleteFile}
                onChange={e => setBulkDeleteFile(e.target.checked)}
              />
              Also delete the files from disk
            </label>
            <div className="modal__actions">
              <button className="btn btn--secondary" onClick={() => setBulkDeleteOpen(false)} disabled={bulkDeleting}>
                Cancel
              </button>
              <button className="btn btn--danger" onClick={handleBulkDelete} disabled={bulkDeleting}>
                {bulkDeleting ? 'Deleting…' : bulkDeleteFile ? `Delete ${selectedIds.size} book${selectedIds.size !== 1 ? 's' : ''} & files` : `Remove ${selectedIds.size} from library`}
              </button>
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
          onBookDeleted={() => {
            setBooks(prev => prev.filter(b => b.id !== selectedBook.id));
            setSelectedBook(null);
          }}
        />
      )}
    </div>
  );
}
