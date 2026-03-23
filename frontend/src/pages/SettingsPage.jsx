import { useEffect, useState } from 'react';
import client from '../api/client';
import { exportCsv, exportJson } from '../api/export';
import { renameBook } from '../api/books';
import './SettingsPage.css';

const LS_KEY = 'librarian_api_key';

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function SettingsPage() {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(LS_KEY) ?? '');
  const [keySaved, setKeySaved] = useState(false);

  const [renameId, setRenameId] = useState('');
  const [renameResult, setRenameResult] = useState(null);
  const [renameLoading, setRenameLoading] = useState(false);
  const [renameError, setRenameError] = useState('');

  const [exportLoading, setExportLoading] = useState({ csv: false, json: false });

  const [health, setHealth] = useState(null);

  useEffect(() => {
    client.get('/api/health')
      .then(r => setHealth(r.data))
      .catch(() => {});
  }, []);

  function handleSaveKey() {
    localStorage.setItem(LS_KEY, apiKey);
    client.defaults.headers['X-API-Key'] = apiKey;
    setKeySaved(true);
    setTimeout(() => setKeySaved(false), 2500);
  }

  async function handleRenamePreview() {
    const id = renameId.trim();
    if (!id) return;
    setRenameLoading(true);
    setRenameResult(null);
    setRenameError('');
    try {
      const r = await renameBook(parseInt(id, 10), true);
      setRenameResult(r.data);
    } catch (err) {
      setRenameError(err?.response?.data?.detail ?? 'Preview failed.');
    } finally {
      setRenameLoading(false);
    }
  }

  async function handleExport(format) {
    setExportLoading(prev => ({ ...prev, [format]: true }));
    try {
      const r = format === 'csv' ? await exportCsv() : await exportJson();
      const ext = format === 'csv' ? 'csv' : 'json';
      downloadBlob(r.data, `librarian_export.${ext}`);
    } catch {
      // ignore
    } finally {
      setExportLoading(prev => ({ ...prev, [format]: false }));
    }
  }

  return (
    <div className="settings-page">
      <h1 className="settings-page__title">Settings</h1>

      {/* ── API Key ── */}
      <section className="settings-section">
        <h2 className="settings-section__heading">API Key</h2>
        <p className="settings-section__warning">
          ⚠ API key is visible in the browser. Local use only.
        </p>
        <div className="settings-row">
          <input
            className="settings-input"
            type="password"
            placeholder="Enter API key…"
            value={apiKey}
            onChange={e => { setApiKey(e.target.value); setKeySaved(false); }}
            onKeyDown={e => e.key === 'Enter' && handleSaveKey()}
          />
          <button
            className="btn btn--primary"
            onClick={handleSaveKey}
            disabled={!apiKey.trim()}
          >
            {keySaved ? 'Saved ✓' : 'Save'}
          </button>
        </div>
      </section>

      {/* ── Ollama info ── */}
      <section className="settings-section">
        <h2 className="settings-section__heading">Ollama</h2>
        {health ? (
          <dl className="settings-dl">
            <dt>Model</dt>
            <dd><code>{health.ollama_model}</code></dd>
            <dt>URL</dt>
            <dd><code>{health.ollama_url}</code></dd>
            <dt>Backend</dt>
            <dd><span className="settings-status settings-status--ok">{health.status}</span></dd>
          </dl>
        ) : (
          <p className="settings-muted">Loading…</p>
        )}
      </section>

      {/* ── Rename preview ── */}
      <section className="settings-section">
        <h2 className="settings-section__heading">Rename Preview</h2>
        <p className="settings-muted">
          Enter a book ID to preview how it would be renamed on disk (dry run).
        </p>
        <div className="settings-row">
          <input
            className="settings-input settings-input--sm"
            type="number"
            placeholder="Book ID"
            value={renameId}
            onChange={e => { setRenameId(e.target.value); setRenameResult(null); setRenameError(''); }}
            onKeyDown={e => e.key === 'Enter' && handleRenamePreview()}
          />
          <button
            className="btn btn--secondary"
            onClick={handleRenamePreview}
            disabled={renameLoading || !renameId.trim()}
          >
            {renameLoading ? 'Previewing…' : 'Preview'}
          </button>
        </div>
        {renameError && <p className="settings-error">{renameError}</p>}
        {renameResult && (
          <div className="rename-result">
            <div className="rename-result__row">
              <span className="rename-result__label">Status</span>
              <span className={`rename-result__value rename-result__value--${renameResult.status}`}>
                {renameResult.status}
              </span>
            </div>
            {renameResult.old_path && (
              <div className="rename-result__row">
                <span className="rename-result__label">Old path</span>
                <code className="rename-result__path">{renameResult.old_path}</code>
              </div>
            )}
            {renameResult.new_path && (
              <div className="rename-result__row">
                <span className="rename-result__label">New path</span>
                <code className="rename-result__path">{renameResult.new_path}</code>
              </div>
            )}
            {renameResult.reason && (
              <div className="rename-result__row">
                <span className="rename-result__label">Reason</span>
                <span className="rename-result__value">{renameResult.reason}</span>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ── Export ── */}
      <section className="settings-section">
        <h2 className="settings-section__heading">Export Library</h2>
        <div className="settings-row">
          <button
            className="btn btn--secondary"
            onClick={() => handleExport('csv')}
            disabled={exportLoading.csv}
          >
            {exportLoading.csv ? 'Exporting…' : 'Export CSV'}
          </button>
          <button
            className="btn btn--secondary"
            onClick={() => handleExport('json')}
            disabled={exportLoading.json}
          >
            {exportLoading.json ? 'Exporting…' : 'Export JSON'}
          </button>
        </div>
      </section>
    </div>
  );
}
