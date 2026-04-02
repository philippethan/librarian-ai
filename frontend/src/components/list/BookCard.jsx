import { getCover } from '../../api/books';
import './BookCard.css';

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

export default function BookCard({ book, onClick, selected, onToggleSelect }) {
  return (
    <div
      className={`book-card${selected ? ' book-card--selected' : ''}`}
      onClick={() => onClick(book)}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && onClick(book)}
    >
      <input
        type="checkbox"
        className="book-card__checkbox"
        checked={!!selected}
        onChange={e => { e.stopPropagation(); onToggleSelect(book.id); }}
        onClick={e => e.stopPropagation()}
        title="Select"
      />
      <div className="book-card__cover">
        <img
          src={getCover(book.id)}
          alt={book.title ?? 'Cover'}
          onError={e => { e.currentTarget.style.display = 'none'; }}
        />
      </div>
      <div className="book-card__body">
        <div className="book-card__title">{book.title ?? book.filename}</div>
        <div className="book-card__author">{book.author ?? '—'}</div>
        <ConfidenceBar score={book.confidence_score ?? 0} />
        <div className="book-card__badges">
          {book.reading_status && (
            <span className={`badge badge--reading-${book.reading_status}`}>
              {book.reading_status}
            </span>
          )}
          {book.duplicate_of && (
            <span className="badge badge--dupe">DUPE</span>
          )}
        </div>
      </div>
    </div>
  );
}
