import { useEffect, useState } from 'react';
import { debugBook } from '../../api/books';

const PATCHABLE = [
  'title', 'author', 'year', 'language', 'category',
  'subcategory', 'difficulty', 'description', 'tags',
];

function isFilled(val) {
  if (val === null || val === undefined) return false;
  if (typeof val === 'string') return val.trim() !== '';
  if (Array.isArray(val)) return val.length > 0;
  return true;
}

function MetaGrid({ fields }) {
  return (
    <div className="dp-debug__grid">
      {PATCHABLE.map(field => {
        const val = fields?.[field] ?? null;
        const filled = isFilled(val);
        return (
          <div key={field} className={`dp-debug__cell dp-debug__cell--${filled ? 'green' : 'red'}`}>
            <span className="dp-debug__field">{field}</span>
            <span className="dp-debug__val">
              {val == null ? '—' : Array.isArray(val) ? val.join(', ') : String(val)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function PassPanel({ label, data, textLength }) {
  const [showRaw, setShowRaw] = useState(false);
  return (
    <details className="dp-debug__accordion">
      <summary className="dp-debug__summary">{label}</summary>
      <div className="dp-debug__panel">
        {textLength !== undefined && (
          <div className="dp-debug__text-length">
            Text extracted: <strong>{textLength.toLocaleString()}</strong> chars
          </div>
        )}
        {data ? (
          <MetaGrid fields={data} />
        ) : (
          <div className="dp-debug__empty-pass">Pass not run.</div>
        )}
        <button
          className="btn btn--sm btn--ghost dp-debug__raw-btn"
          onClick={() => setShowRaw(s => !s)}
        >
          {showRaw ? 'Hide Raw JSON' : 'Raw JSON'}
        </button>
        {showRaw && (
          <pre className="dp-debug__raw">{JSON.stringify(data, null, 2)}</pre>
        )}
      </div>
    </details>
  );
}

export default function DebugTab({ book }) {
  const [debug, setDebug] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notProcessed, setNotProcessed] = useState(false);
  const [showMergedRaw, setShowMergedRaw] = useState(false);

  useEffect(() => {
    setLoading(true);
    setNotProcessed(false);
    setDebug(null);
    setShowMergedRaw(false);
    debugBook(book.id)
      .then(r => setDebug(r.data))
      .catch(err => {
        if (err?.response?.status === 404) setNotProcessed(true);
      })
      .finally(() => setLoading(false));
  }, [book.id]);

  if (loading) return <div className="dp-debug__loading">Loading debug info…</div>;
  if (notProcessed) return <div className="dp-debug__empty">Not yet processed</div>;
  if (!debug) return <div className="dp-debug__empty">No debug data available.</div>;

  const passes = debug.passes ?? {};
  const merged = debug.merged ?? {};
  const filledCount = PATCHABLE.filter(f => isFilled(merged[f])).length;

  return (
    <div className="dp-debug">
      <PassPanel
        label="pdfplumber"
        data={passes.pdfplumber || null}
        textLength={debug.pdfplumber_text_length}
      />
      <PassPanel
        label="ocr"
        data={passes.ocr || null}
        textLength={debug.ocr_text_length}
      />
      <PassPanel
        label="filename_heuristic"
        data={passes.filename_heuristic || null}
      />

      <details className="dp-debug__accordion dp-debug__accordion--merged">
        <summary className="dp-debug__summary">
          merged
          <span className="dp-debug__summary-badge">
            {filledCount}/9 fields · confidence {merged.confidence_score?.toFixed(2) ?? '—'}
          </span>
        </summary>
        <div className="dp-debug__panel">
          <div className="dp-debug__conf-row">
            <span>Fields filled: <strong>{filledCount}/9</strong></span>
            <span>Confidence score: <strong>{merged.confidence_score?.toFixed(4) ?? '—'}</strong></span>
          </div>
          <MetaGrid fields={merged} />
          <button
            className="btn btn--sm btn--ghost dp-debug__raw-btn"
            onClick={() => setShowMergedRaw(s => !s)}
          >
            {showMergedRaw ? 'Hide Raw JSON' : 'Raw JSON'}
          </button>
          {showMergedRaw && (
            <pre className="dp-debug__raw">{JSON.stringify(merged, null, 2)}</pre>
          )}
        </div>
      </details>
    </div>
  );
}
