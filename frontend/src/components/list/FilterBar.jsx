import { useEffect, useRef, useState } from 'react';
import { listShelves } from '../../api/shelves';
import './FilterBar.css';

export default function FilterBar({ filters, onFiltersChange, viewMode, onViewModeChange, onScan, onClean }) {
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
        <div className="filter-bar__view-toggle">
          <button
            className={`filter-bar__view-btn${viewMode === 'table' ? ' active' : ''}`}
            onClick={() => onViewModeChange('table')}
            title="Table view"
            aria-label="Table view"
          >
            ☰
          </button>
          <button
            className={`filter-bar__view-btn${viewMode === 'card' ? ' active' : ''}`}
            onClick={() => onViewModeChange('card')}
            title="Card view"
            aria-label="Card view"
          >
            ⊞
          </button>
        </div>
        <button className="filter-bar__scan-btn" onClick={onScan}>
          Scan
        </button>
        <button className="filter-bar__scan-btn filter-bar__clean-btn" onClick={onClean} title="Remove z-library / 1lib / z-lib watermarks from all filenames">
          Clean Filenames
        </button>
      </div>
    </div>
  );
}
