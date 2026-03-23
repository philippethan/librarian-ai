import { useEffect, useState } from 'react';
import { deleteBook } from '../api/books';
import { dismissGroup, listDuplicates } from '../api/duplicates';
import './DuplicatesPage.css';

function formatBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function Toast({ message, onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000);
    return () => clearTimeout(t);
  }, [onDone]);
  return <div className="dup-toast">{message}</div>;
}

export default function DuplicatesPage() {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleteFile, setDeleteFile] = useState({});   // bookId -> bool
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState({});               // groupHash|bookId -> bool

  function fetchGroups() {
    setLoading(true);
    listDuplicates()
      .then(r => setGroups(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => { fetchGroups(); }, []);

  function toggleDeleteFile(bookId) {
    setDeleteFile(prev => ({ ...prev, [bookId]: !prev[bookId] }));
  }

  async function handleDeleteBook(groupHash, bookId) {
    const key = `book-${bookId}`;
    setBusy(prev => ({ ...prev, [key]: true }));
    try {
      await deleteBook(bookId, !!deleteFile[bookId]);
      setGroups(prev =>
        prev
          .map(g => g.hash === groupHash
            ? { ...g, books: g.books.filter(b => b.id !== bookId) }
            : g
          )
          .filter(g => g.books.length > 0)
      );
    } catch {
      // ignore
    } finally {
      setBusy(prev => ({ ...prev, [key]: false }));
    }
  }

  async function handleDismiss(hash) {
    const key = `dismiss-${hash}`;
    setBusy(prev => ({ ...prev, [key]: true }));
    try {
      await dismissGroup(hash);
      setGroups(prev => prev.filter(g => g.hash !== hash));
      setToast('Group dismissed — these files will not be flagged again.');
    } catch {
      // ignore
    } finally {
      setBusy(prev => ({ ...prev, [key]: false }));
    }
  }

  return (
    <div className="dup-page">
      <div className="dup-page__header">
        <h1 className="dup-page__title">Duplicates</h1>
        {!loading && (
          <span className="dup-page__subtitle">
            {groups.length} {groups.length === 1 ? 'group' : 'groups'}
          </span>
        )}
      </div>

      {loading && <div className="dup-page__loading">Loading…</div>}

      {!loading && groups.length === 0 && (
        <div className="dup-page__empty">No duplicate groups found.</div>
      )}

      {!loading && groups.length > 0 && (
        <div className="dup-list">
          {groups.map(group => (
            <div key={group.hash} className="dup-group">
              <div className="dup-group__head">
                <span className="dup-group__hash" title={group.hash}>
                  {group.hash.slice(0, 8)}…
                </span>
                <span className="dup-group__count">
                  {group.books.length} files
                </span>
                <button
                  className="btn btn--secondary dup-group__dismiss"
                  disabled={!!busy[`dismiss-${group.hash}`]}
                  onClick={() => handleDismiss(group.hash)}
                >
                  {busy[`dismiss-${group.hash}`] ? 'Dismissing…' : 'Dismiss Group'}
                </button>
              </div>

              <table className="dup-table">
                <thead>
                  <tr>
                    <th className="dup-table__th">Filename</th>
                    <th className="dup-table__th">Path</th>
                    <th className="dup-table__th dup-table__th--size">Size</th>
                    <th className="dup-table__th dup-table__th--del">Also delete file</th>
                    <th className="dup-table__th"></th>
                  </tr>
                </thead>
                <tbody>
                  {group.books.map(book => (
                    <tr key={book.id} className="dup-table__row">
                      <td className="dup-table__cell dup-table__cell--name">
                        {book.filename}
                      </td>
                      <td className="dup-table__cell dup-table__cell--path">
                        {book.filepath}
                      </td>
                      <td className="dup-table__cell dup-table__cell--size">
                        {formatBytes(book.filesize)}
                      </td>
                      <td className="dup-table__cell dup-table__cell--check">
                        <input
                          type="checkbox"
                          checked={!!deleteFile[book.id]}
                          onChange={() => toggleDeleteFile(book.id)}
                          title="Also delete file from disk"
                        />
                      </td>
                      <td className="dup-table__cell dup-table__cell--action">
                        <button
                          className="btn btn--danger-sm"
                          disabled={!!busy[`book-${book.id}`]}
                          onClick={() => handleDeleteBook(group.hash, book.id)}
                        >
                          {busy[`book-${book.id}`] ? '…' : 'Delete'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}

      {toast && (
        <Toast message={toast} onDone={() => setToast(null)} />
      )}
    </div>
  );
}
