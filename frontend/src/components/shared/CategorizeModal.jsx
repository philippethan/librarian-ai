import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { bulkCategorize, categorizeDocument } from '../../api/books';

// Inline the taxonomy so we don't need an extra fetch.
// Keep in sync with FIXED_TAXONOMY in backend/app.py.
const TAXONOMY = {
  "Personal Development": ["Self-help", "Psychology", "Mental Health", "Relationships", "Communication", "Emotional Intelligence", "Productivity", "Time Management", "Motivation", "Finance & Money", "Career Development", "Spirituality"],
  "Literature & Fiction": ["Novels", "Romance", "Contemporary Fiction", "Memoir", "Short Stories", "Thriller", "Young Adult", "World Literature"],
  "Language Learning": ["French", "English", "Grammar", "Vocabulary", "Exam Preparation", "Dictionaries", "Conversation", "Linguistics"],
  "Science": ["Physics", "Chemistry", "Biology", "Earth Science", "General Science"],
  "Mathematics": ["Calculus", "Algebra", "Statistics", "Geometry", "Optimization", "Trigonometry"],
  "Computer & Software": ["Programming", "Software Engineering", "Computer Science", "AI & Machine Learning", "Cybersecurity", "Networking", "Data Engineering", "Cloud Computing"],
  "Engineering": ["Mechanical Engineering", "Electrical Engineering", "Electronics & Embedded Systems", "Systems Engineering", "Civil Engineering", "Aerospace Engineering", "Transport Engineering"],
  "Business & Management": ["Entrepreneurship", "Leadership", "Management", "Marketing", "Finance", "Economics", "Project Management", "Communication Skills"],
  "History & Politics": ["Modern History", "Asian History", "Cambodian History", "Islamic History", "Politics", "International Relations"],
  "Health & Medicine": ["Nutrition", "Mental Health", "Anatomy", "Medical Education", "Sexual Health"],
  "Philosophy": ["Stoicism", "Ancient Philosophy", "Metaphysics", "Ethics"],
  "Law": ["Contract Law", "Employment Law", "Intellectual Property Law"],
  "Career & Job Hunting": ["Job Search", "Career Guides"],
  "Sexuality & Relationships": ["LGBTQ+", "Female Sexuality", "Adolescent Sexuality"],
  "Tourism & Travel": ["Travel Guides", "Tourism Management"],
  "Communication & Writing": ["Academic Writing", "Technical Writing", "Professional Communication", "Essay & Memoir"],
  "Art & Design": ["Visual Arts", "Architecture", "UX Design"],
  "Miscellaneous": ["Uncategorized", "Other"],
};

/**
 * CategorizeModal
 *
 * Props:
 *   bookId      {number|null}   - single-book mode (mutually exclusive with bookIds)
 *   bookIds     {number[]|null} - bulk mode
 *   onClose     {() => void}
 *   onSaved     {(result) => void}  - called after a successful save
 *   categorizeKey {string}  - value of X-Categorize-Key (empty = no key needed)
 */
export default function CategorizeModal({ bookId, bookIds, onClose, onSaved, categorizeKey = '' }) {
  const isBulk = Array.isArray(bookIds) && bookIds.length > 0;
  const [selectedCategory, setSelectedCategory] = useState('');
  const [selectedSubcategory, setSelectedSubcategory] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const subcategories = selectedCategory ? (TAXONOMY[selectedCategory] ?? []) : [];

  // Reset subcategory when category changes
  useEffect(() => {
    setSelectedSubcategory('');
  }, [selectedCategory]);

  async function handleConfirm() {
    if (!selectedCategory) {
      setError('Please select a category.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      let result;
      if (isBulk) {
        const res = await bulkCategorize(bookIds, selectedCategory, selectedSubcategory, categorizeKey);
        result = res.data;
      } else {
        const res = await categorizeDocument(bookId, selectedCategory, selectedSubcategory, categorizeKey);
        result = res.data;
      }
      onSaved(result);
      onClose();
    } catch (err) {
      setError(err?.response?.data?.detail ?? 'Categorisation failed.');
    } finally {
      setLoading(false);
    }
  }

  function handleBackdrop(e) {
    if (e.target === e.currentTarget) onClose();
  }

  return createPortal(
    <div className="modal-backdrop" onClick={handleBackdrop} style={backdropStyle}>
      <div className="modal-box" style={boxStyle}>
        <h2 style={{ marginTop: 0, marginBottom: 16, fontSize: 18 }}>
          {isBulk ? `Categorize ${bookIds.length} book${bookIds.length !== 1 ? 's' : ''}` : 'Categorize book'}
        </h2>

        <label style={labelStyle}>Category</label>
        <select
          value={selectedCategory}
          onChange={e => setSelectedCategory(e.target.value)}
          style={selectStyle}
        >
          <option value="">-- select --</option>
          {Object.keys(TAXONOMY).map(cat => (
            <option key={cat} value={cat}>{cat}</option>
          ))}
        </select>

        {subcategories.length > 0 && (
          <>
            <label style={{ ...labelStyle, marginTop: 12 }}>Subcategory</label>
            <select
              value={selectedSubcategory}
              onChange={e => setSelectedSubcategory(e.target.value)}
              style={selectStyle}
            >
              <option value="">-- none --</option>
              {subcategories.map(sub => (
                <option key={sub} value={sub}>{sub}</option>
              ))}
            </select>
          </>
        )}

        {error && <p style={{ color: '#e55', marginTop: 10, marginBottom: 0 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 8, marginTop: 20, justifyContent: 'flex-end' }}>
          <button className="btn btn--ghost" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button
            className="btn btn--primary"
            onClick={handleConfirm}
            disabled={loading || !selectedCategory}
          >
            {loading ? 'Saving…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

const backdropStyle = {
  position: 'fixed', inset: 0,
  background: 'rgba(0,0,0,0.45)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  zIndex: 1000,
};

const boxStyle = {
  background: 'var(--surface, #1e2030)',
  border: '1px solid var(--border, #3a3f5c)',
  borderRadius: 8,
  padding: '24px 28px',
  minWidth: 320,
  maxWidth: 420,
  width: '100%',
  color: 'var(--text, #cdd6f4)',
};

const labelStyle = {
  display: 'block',
  fontSize: 13,
  fontWeight: 600,
  marginBottom: 4,
  color: 'var(--text-muted, #a6adc8)',
};

const selectStyle = {
  width: '100%',
  padding: '6px 10px',
  borderRadius: 5,
  border: '1px solid var(--border, #3a3f5c)',
  background: 'var(--surface2, #181926)',
  color: 'var(--text, #cdd6f4)',
  fontSize: 14,
};
