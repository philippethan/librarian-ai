import './DetailPanel.css';

export default function DetailPanel({ book, onClose, onBookUpdated }) {
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
        <div className="detail-panel__body">
          <p className="detail-panel__coming-soon">
            Detail panel — coming soon
          </p>
        </div>
      </div>
    </div>
  );
}
