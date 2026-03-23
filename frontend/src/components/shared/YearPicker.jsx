import { useState } from 'react';

const MIN_YEAR = 1800;
const MAX_YEAR = new Date().getFullYear() + 1;

export default function YearPicker({ value, onChange }) {
  const [draft, setDraft] = useState(value != null ? String(value) : '');
  const [error, setError] = useState(false);

  function validate(raw) {
    const s = raw.trim();
    if (!s) { setError(false); onChange(null); return; }
    const n = parseInt(s, 10);
    if (isNaN(n) || n < MIN_YEAR || n > MAX_YEAR) {
      setError(true);
      setDraft('');
      onChange(null);
    } else {
      setError(false);
      onChange(n);
    }
  }

  function handleBlur() {
    validate(draft);
  }

  function handleKeyDown(e) {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const current = parseInt(draft, 10);
      const next = isNaN(current) ? new Date().getFullYear() : current + 1;
      if (next <= MAX_YEAR) { setDraft(String(next)); setError(false); onChange(next); }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const current = parseInt(draft, 10);
      const next = isNaN(current) ? new Date().getFullYear() : current - 1;
      if (next >= MIN_YEAR) { setDraft(String(next)); setError(false); onChange(next); }
    }
  }

  // Keep draft in sync when value changes externally
  function handleChange(e) {
    setDraft(e.target.value);
    setError(false);
  }

  return (
    <div className="year-picker">
      <input
        className={`form-input${error ? ' form-input--error' : ''}`}
        value={draft}
        onChange={handleChange}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        placeholder={`${MIN_YEAR}–${MAX_YEAR}`}
        inputMode="numeric"
      />
      {error && <span className="year-picker__error">Invalid year</span>}
    </div>
  );
}
