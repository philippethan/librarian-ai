import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { listShelves } from '../../api/shelves';
import './FilterBar.css';

function ColPicker({ allCols, visibleCols, onToggleCol }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btnRef = useRef(null);
  const popupRef = useRef(null);

  function handleToggle() {
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
      <button ref={btnRef} className="filter-bar__scan-btn" onClick={handleToggle} title="Show/hide columns">
        Columns ▾
      </button>
      {open && createPortal(
        <div ref={popupRef} className="col-picker__popup" style={{ position: 'fixed', top: pos.top, left: pos.left }}>
          {allCols.map(c => (
            <label key={c.key} className="col-picker__row">
              <input
                type="checkbox"
                checked={visibleCols.has(c.key)}
                onChange={() => onToggleCol(c.key)}
              />
              {c.label}
            </label>
          ))}
        </div>,
        document.body
      )}
    </>
  );
}

export default function FilterBar({ filters, onFiltersChange, onScan, onClean, allCols, visibleCols, onToggleCol }) {
  const [shelves, setShelves] = useState([]);
  const debounceRef = useRef(null);

  useEffect(() => {
    listShelves().then(r => setShelves(r.data)).catch(() => {});
  }, []);

  function handleSearch(e) {
    const val = e.target.value;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      onFiltersChange({ ...filters, search: val });
    }, 300);
  }

  function handleSelect(field, value) {
    onFiltersChange({ ...filters, [field]: value });
  }

  return (
    <div className="filter-bar">
      <div className="filter-bar__left">
        <input
          className="filter-bar__search"
          type="text"
          placeholder="Search books…"
          defaultValue={filters.search ?? ''}
          onChange={handleSearch}
        />
        <select
          className="filter-bar__select"
          value={filters.status ?? ''}
          onChange={e => handleSelect('status', e.target.value)}
        >
          <option value="">All statuses</option>
          <option value="done">Done</option>
          <option value="partial">Partial</option>
          <option value="processing">Processing</option>
          <option value="error">Error</option>
          <option value="duplicate">Duplicate</option>
        </select>
        <select
          className="filter-bar__select"
          value={filters.extraction_method ?? ''}
          onChange={e => handleSelect('extraction_method', e.target.value)}
        >
          <option value="">All methods</option>
          <option value="pdfplumber">pdfplumber</option>
          <option value="ocr">OCR</option>
          <option value="filename_heuristic">Filename</option>
          <option value="merged">Merged</option>
          <option value="ebooklib">ebooklib</option>
        </select>
        <select
          className="filter-bar__select"
          value={filters.reading_status ?? ''}
          onChange={e => handleSelect('reading_status', e.target.value)}
        >
          <option value="">All reading</option>
          <option value="to-read">To-read</option>
          <option value="reading">Reading</option>
          <option value="done">Done</option>
        </select>
        <select
          className="filter-bar__select"
          value={filters.shelf_id ?? ''}
          onChange={e => handleSelect('shelf_id', e.target.value)}
        >
          <option value="">All shelves</option>
          {shelves.map(s => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </div>
      <div className="filter-bar__right">
        <button className="filter-bar__scan-btn" onClick={onScan}>
          Scan
        </button>
        <button className="filter-bar__scan-btn filter-bar__clean-btn" onClick={onClean} title="Remove z-library / 1lib / z-lib watermarks from all filenames">
          Clean Filenames
        </button>
        {allCols && visibleCols && onToggleCol && (
          <ColPicker allCols={allCols} visibleCols={visibleCols} onToggleCol={onToggleCol} />
        )}
      </div>
    </div>
  );
}
