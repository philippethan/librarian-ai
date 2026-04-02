import { useEffect, useRef, useState } from 'react';
import {
  patchBook, fixBook, enrichBook, renameBook, renameBookAs, renameSuggest,
  openBook, refreshCover, getCover, patchReadingStatus, getBook,
} from '../../api/books';
import { listShelves, addBookToShelf, removeBookFromShelf } from '../../api/shelves';
import CategoryComboBox from '../shared/CategoryComboBox';
import LanguagePicker from '../shared/LanguagePicker';
import SegmentedControl from '../shared/SegmentedControl';
import YearPicker from '../shared/YearPicker';
import DebugTab from './DebugTab';
import './DetailPanel.css';

// ── helpers ───────────────────────────────────────────────────────────────────

function formFromBook(b) {
  return {
    title:       b.title       ?? '',
    author:      b.author      ?? '',
    year:        b.year        ?? null,
    language:    b.language    ?? null,
    category:    b.category    ?? null,
    subcategory: b.subcategory ?? null,
    difficulty:  b.difficulty  ?? null,
    description: b.description ?? '',
    tags:        Array.isArray(b.tags) ? b.tags.join(', ') : '',
  };
}

function tagsFromString(s) {
  return s.split(',').map(t => t.trim().toLowerCase()).filter(Boolean);
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function useToast() {
  const [toasts, setToasts] = useState([]);
  function addToast(msg, kind = 'info') {
    const id = Date.now();
    setToasts(prev => [...prev, { id, msg, kind }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  }
  return { toasts, addToast };
}

function ToastList({ toasts }) {
  return (
    <div className="dp-toasts">
      {toasts.map(t => (
        <div key={t.id} className={`dp-toast dp-toast--${t.kind}`}>{t.msg}</div>
      ))}
    </div>
  );
}

// ── ShelfManager ──────────────────────────────────────────────────────────────

function ShelfManager({ bookId, bookShelves, onChanged }) {
  const [allShelves, setAllShelves] = useState([]);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    listShelves().then(r => setAllShelves(r.data)).catch(() => {});
  }, []);

  const currentIds = new Set((bookShelves ?? []).map(s => s.id));
  const available = allShelves.filter(s => !currentIds.has(s.id));

  async function handleAdd(e) {
    const shelfId = parseInt(e.target.value, 10);
    if (!shelfId) return;
    try {
      await addBookToShelf(shelfId, bookId);
      onChanged();
    } catch {}
    setAdding(false);
  }

  async function handleRemove(shelfId) {
    try {
      await removeBookFromShelf(shelfId, bookId);
      onChanged();
    } catch {}
  }

  return (
    <div className="shelf-manager">
      <div className="shelf-manager__chips">
        {(bookShelves ?? []).map(s => (
          <span key={s.id} className="shelf-chip">
            {s.name}
            <button
              className="shelf-chip__remove"
              onClick={() => handleRemove(s.id)}
              aria-label={`Remove from ${s.name}`}
            >×</button>
          </span>
        ))}
        {(bookShelves ?? []).length === 0 && (
          <span className="shelf-manager__empty">No shelves</span>
        )}
      </div>
      {adding ? (
        <select className="form-select form-select--sm" onChange={handleAdd} autoFocus defaultValue="">
          <option value="">— pick shelf —</option>
          {available.map(s => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      ) : (
        available.length > 0 && (
          <button className="btn btn--sm btn--ghost" onClick={() => setAdding(true)}>
            + Add to shelf
          </button>
        )
      )}
    </div>
  );
}

// ── EditTab ───────────────────────────────────────────────────────────────────

function EditTab({ book, onBookUpdated, addToast }) {
  const [form, setForm] = useState(() => formFromBook(book));
  const [autoLock, setAutoLock] = useState(true);
  const [saving, setSaving] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [renamePreview, setRenamePreview] = useState(null);
  const [renameInput, setRenameInput] = useState('');
  const [coverKey, setCoverKey] = useState(0);
  const [currentBook, setCurrentBook] = useState(book);
  const originalRef = useRef({ title: book.title ?? '', author: book.author ?? '' });

  // Reset form when book changes
  useEffect(() => {
    setForm(formFromBook(book));
    setCurrentBook(book);
    originalRef.current = { title: book.title ?? '', author: book.author ?? '' };
  }, [book.id]);

  function reloadBook() {
    getBook(book.id).then(r => {
      setCurrentBook(r.data);
      onBookUpdated(r.data);
    }).catch(() => {});
  }

  // ── Save ──────────────────────────────────────────────────────────────────

  async function handleSave() {
    setSaving(true);
    try {
      const payload = {
        title:       form.title       || null,
        author:      form.author      || null,
        year:        form.year,
        language:    form.language,
        category:    form.category,
        subcategory: form.subcategory,
        difficulty:  form.difficulty,
        description: form.description || null,
        tags:        tagsFromString(form.tags),
      };
      await patchBook(book.id, payload);

      const titleChanged  = form.title  !== originalRef.current.title;
      const authorChanged = form.author !== originalRef.current.author;
      if (autoLock && (titleChanged || authorChanged)) {
        await fixBook(book.id).catch(() => {});
      }

      originalRef.current = { title: form.title, author: form.author };
      reloadBook();
      addToast('Saved', 'success');
    } catch (err) {
      addToast(err?.response?.data?.detail ?? 'Save failed', 'error');
    } finally {
      setSaving(false);
    }
  }

  // ── Save & Rename ─────────────────────────────────────────────────────────

  async function handleSaveRename() {
    setSaving(true);
    try {
      const payload = {
        title:       form.title       || null,
        author:      form.author      || null,
        year:        form.year,
        language:    form.language,
        category:    form.category,
        subcategory: form.subcategory,
        difficulty:  form.difficulty,
        description: form.description || null,
        tags:        tagsFromString(form.tags),
      };
      await patchBook(book.id, payload);
      const preview = await renameBook(book.id, true);
      if (preview.data?.skipped) {
        addToast(`Rename skipped: ${preview.data.reason}`, 'info');
        reloadBook();
        setSaving(false);
        return;
      }
      setRenameInput(preview.data.new_filename ?? '');
      setRenamePreview(preview.data);
    } catch (err) {
      addToast(err?.response?.data?.detail ?? 'Save failed', 'error');
      setSaving(false);
    }
  }

  async function confirmRename() {
    try {
      await renameBookAs(book.id, renameInput);
      setRenamePreview(null);
      reloadBook();
      addToast('File renamed', 'success');
    } catch (err) {
      addToast(err?.response?.data?.detail ?? 'Rename failed', 'error');
    } finally {
      setSaving(false);
    }
  }

  // ── Enrich ────────────────────────────────────────────────────────────────

  async function handleEnrich() {
    setEnriching(true);
    try {
      await enrichBook(book.id);
      reloadBook();
      addToast('Enriched from Open Library', 'success');
    } catch (err) {
      addToast(err?.response?.data?.detail ?? 'Enrichment failed', 'error');
    } finally {
      setEnriching(false);
    }
  }

  // ── Suggest Rename ────────────────────────────────────────────────────────

  async function handleSuggestRename() {
    try {
      const res = await renameSuggest(book.id);
      const suggested = res.data.suggested_filename ?? '';
      setRenameInput(suggested);
      setRenamePreview({ new_filename: suggested, old_filename: book.filename });
    } catch (err) {
      addToast(err?.response?.data?.detail ?? 'Could not generate suggestion', 'error');
    }
  }

  // ── Open File ─────────────────────────────────────────────────────────────

  async function handleOpen() {
    try {
      await openBook(book.id);
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail ?? 'Could not open file';
      addToast(detail, status === 403 || status === 404 ? 'error' : 'error');
    }
  }

  // ── Reading Status ────────────────────────────────────────────────────────

  async function handleReadingStatus(e) {
    const val = e.target.value || null;
    try {
      await patchReadingStatus(book.id, val);
      reloadBook();
    } catch (err) {
      addToast(err?.response?.data?.detail ?? 'Status update failed', 'error');
    }
  }

  // ── Refresh Cover ─────────────────────────────────────────────────────────

  async function handleRefreshCover() {
    try {
      await refreshCover(book.id);
      setCoverKey(k => k + 1);
      addToast('Cover refreshed', 'success');
    } catch {
      addToast('Cover refresh failed', 'error');
    }
  }

  const coverUrl = getCover(book.id);

  return (
    <div className="dp-edit">
      {/* Cover + reading status row */}
      <div className="dp-edit__top-row">
        <div className="dp-edit__main-fields">
          <div className="dp-edit__reading-row">
            <label className="dp-edit__label">Reading status</label>
            <select
              className="form-select"
              value={currentBook.reading_status ?? ''}
              onChange={handleReadingStatus}
            >
              <option value="">— not set —</option>
              <option value="to-read">To read</option>
              <option value="reading">Reading</option>
              <option value="done">Done</option>
            </select>
          </div>
        </div>
        <div className="dp-edit__cover-col">
          <img
            key={coverKey}
            src={`${coverUrl}?v=${coverKey}`}
            alt="Cover"
            className="dp-edit__cover"
            onError={e => { e.currentTarget.style.display = 'none'; }}
          />
          <button className="btn btn--sm btn--ghost dp-edit__cover-refresh" onClick={handleRefreshCover}>
            Refresh Cover
          </button>
        </div>
      </div>

      {/* Core fields */}
      <div className="dp-edit__field">
        <label className="dp-edit__label">Title</label>
        <input className="form-input" value={form.title ?? ''} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Author</label>
        <input className="form-input" value={form.author ?? ''} onChange={e => setForm(f => ({ ...f, author: e.target.value }))} />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Year</label>
        <YearPicker
          value={form.year}
          onChange={val => setForm(f => ({ ...f, year: val }))}
        />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Language</label>
        <LanguagePicker
          value={form.language}
          onChange={val => setForm(f => ({ ...f, language: val }))}
        />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Category / Subcategory</label>
        <CategoryComboBox
          category={form.category}
          subcategory={form.subcategory}
          onChange={({ category, subcategory }) => setForm(f => ({ ...f, category, subcategory }))}
        />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Difficulty</label>
        <SegmentedControl
          value={form.difficulty}
          onChange={val => setForm(f => ({ ...f, difficulty: val }))}
        />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Tags <span className="dp-edit__hint">(comma-separated)</span></label>
        <input className="form-input" value={form.tags ?? ''} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))} />
      </div>

      <div className="dp-edit__field">
        <label className="dp-edit__label">Description</label>
        <textarea className="form-input form-textarea" value={form.description ?? ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={4} />
      </div>

      {/* Shelves */}
      <div className="dp-edit__field">
        <label className="dp-edit__label">Shelves</label>
        <ShelfManager
          bookId={book.id}
          bookShelves={currentBook.shelves}
          onChanged={reloadBook}
        />
      </div>

      {/* Auto-lock */}
      <label className="dp-edit__autolock">
        <input
          type="checkbox"
          checked={autoLock}
          onChange={e => setAutoLock(e.target.checked)}
        />
        Auto-lock metadata after title/author change
      </label>

      {/* Rename confirmation dialog */}
      {renamePreview && (
        <div className="dp-rename-preview">
          <p className="dp-rename-preview__msg">Rename file to:</p>
          <input
            className="form-input dp-rename-preview__input"
            value={renameInput}
            onChange={e => setRenameInput(e.target.value)}
            autoFocus
            onKeyDown={e => { if (e.key === 'Enter') confirmRename(); }}
          />
          <p className="dp-rename-preview__hint">
            Was: <span>{renamePreview.old_filename}</span>
          </p>
          <div className="dp-rename-preview__actions">
            <button className="btn btn--sm btn--secondary" onClick={() => { setRenamePreview(null); setSaving(false); }}>
              Cancel
            </button>
            <button className="btn btn--sm btn--primary" onClick={confirmRename} disabled={!renameInput.trim()}>
              Confirm Rename
            </button>
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div className="dp-edit__actions">
        <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button className="btn btn--secondary" onClick={handleSaveRename} disabled={saving}>
          Save & Rename File
        </button>
        <button className="btn btn--secondary" onClick={handleSuggestRename} disabled={saving}>
          Suggest Rename
        </button>
        <button className="btn btn--secondary" onClick={handleEnrich} disabled={enriching}>
          {enriching ? 'Enriching…' : 'Enrich with Open Library'}
        </button>
        <button className="btn btn--secondary" onClick={handleOpen}>
          Open File
        </button>
      </div>
    </div>
  );
}

// ── DetailPanel ───────────────────────────────────────────────────────────────

export default function DetailPanel({ book, onClose, onBookUpdated }) {
  const [tab, setTab] = useState('edit');
  const { toasts, addToast } = useToast();

  return (
    <div className="detail-panel-overlay" onClick={onClose}>
      <div className="detail-panel" onClick={e => e.stopPropagation()}>
        <div className="detail-panel__header">
          <h2 className="detail-panel__title">
            {book.title ?? book.filename}
          </h2>
          <button
            className="detail-panel__close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="detail-panel__tabs">
          <button
            className={`detail-panel__tab${tab === 'edit' ? ' detail-panel__tab--active' : ''}`}
            onClick={() => setTab('edit')}
          >
            Edit
          </button>
          <button
            className={`detail-panel__tab${tab === 'debug' ? ' detail-panel__tab--active' : ''}`}
            onClick={() => setTab('debug')}
          >
            Debug
          </button>
        </div>

        <div className="detail-panel__body">
          {tab === 'edit' && (
            <EditTab book={book} onBookUpdated={onBookUpdated} addToast={addToast} />
          )}
          {tab === 'debug' && (
            <DebugTab book={book} />
          )}
        </div>

        <ToastList toasts={toasts} />
      </div>
    </div>
  );
}
