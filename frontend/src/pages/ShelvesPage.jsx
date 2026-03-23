import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createShelf, deleteShelf, listShelves } from '../api/shelves';
import './ShelvesPage.css';

export default function ShelvesPage() {
  const [shelves, setShelves] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [confirmId, setConfirmId] = useState(null);
  const [error, setError] = useState('');
  const inputRef = useRef(null);
  const navigate = useNavigate();

  function fetchShelves() {
    setLoading(true);
    listShelves()
      .then(r => setShelves(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => { fetchShelves(); }, []);

  async function handleCreate(e) {
    if (e.key !== 'Enter') return;
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setError('');
    try {
      await createShelf({ name });
      setNewName('');
      fetchShelves();
    } catch (err) {
      setError(err?.response?.data?.detail ?? 'Could not create shelf.');
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id) {
    try {
      await deleteShelf(id);
      setShelves(prev => prev.filter(s => s.id !== id));
    } catch {
      // ignore
    } finally {
      setConfirmId(null);
    }
  }

  return (
    <div className="shelves-page">
      <div className="shelves-page__header">
        <h1 className="shelves-page__title">Shelves</h1>
        <div className="shelves-page__new">
          <input
            ref={inputRef}
            className="shelves-page__input"
            type="text"
            placeholder="New shelf name…"
            value={newName}
            onChange={e => { setNewName(e.target.value); setError(''); }}
            onKeyDown={handleCreate}
            disabled={creating}
          />
          {error && <span className="shelves-page__error">{error}</span>}
        </div>
      </div>

      {loading && <div className="shelves-page__loading">Loading…</div>}

      {!loading && shelves.length === 0 && (
        <div className="shelves-page__empty">
          No shelves yet. Type a name above and press Enter to create one.
        </div>
      )}

      {!loading && shelves.length > 0 && (
        <div className="shelf-grid">
          {shelves.map(shelf => (
            <div
              key={shelf.id}
              className="shelf-card"
              onClick={() => navigate(`/shelves/${shelf.id}`)}
              tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && navigate(`/shelves/${shelf.id}`)}
              role="button"
            >
              <div className="shelf-card__body">
                <span className="shelf-card__name">{shelf.name}</span>
                <span className="shelf-card__count">
                  {shelf.book_count} {shelf.book_count === 1 ? 'book' : 'books'}
                </span>
              </div>
              <button
                className="shelf-card__delete btn btn--danger-ghost"
                title="Delete shelf"
                onClick={e => { e.stopPropagation(); setConfirmId(shelf.id); }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {confirmId !== null && (
        <div className="modal-overlay" onClick={() => setConfirmId(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Delete shelf?</h2>
            <p className="modal__body">
              This will remove the shelf and all its book associations. Books themselves
              are not deleted.
            </p>
            <div className="modal__actions">
              <button className="btn btn--secondary" onClick={() => setConfirmId(null)}>
                Cancel
              </button>
              <button className="btn btn--danger" onClick={() => handleDelete(confirmId)}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
