import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { getCover } from '../api/books';
import { getShelfBooks } from '../api/shelves';
import DetailPanel from '../components/detail/DetailPanel';
import '../components/list/BookList.css';
import './ShelfDetail.css';

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

export default function ShelfDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [books, setBooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedBook, setSelectedBook] = useState(null);
  const [shelfName, setShelfName] = useState('');

  useEffect(() => {
    setLoading(true);
    getShelfBooks(id)
      .then(r => setBooks(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  return (
    <div className="shelf-detail-page">
      <div className="shelf-detail-page__header">
        <button
          className="btn btn--secondary shelf-detail-page__back"
          onClick={() => navigate('/shelves')}
        >
          ← Shelves
        </button>
        <h1 className="shelf-detail-page__title">Shelf #{id}</h1>
        <span className="shelf-detail-page__count">
          {loading ? '' : `${books.length} ${books.length === 1 ? 'book' : 'books'}`}
        </span>
      </div>

      {loading && <div className="book-list-page__loading">Loading…</div>}

      {!loading && (
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
                  <td colSpan={8} className="book-table__empty">No books on this shelf.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {selectedBook && (
        <DetailPanel
          book={selectedBook}
          onClose={() => setSelectedBook(null)}
          onBookUpdated={updated => {
            setBooks(prev => prev.map(b => b.id === updated.id ? updated : b));
            setSelectedBook(updated);
          }}
        />
      )}
    </div>
  );
}
