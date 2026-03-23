import { useState, useEffect } from 'react';
import { getCategories, addCategory } from '../../api/books';

export default function CategoryComboBox({ category, subcategory, onChange }) {
  const [cats, setCats] = useState([]);
  const [addingCat, setAddingCat] = useState(false);
  const [addingSub, setAddingSub] = useState(false);
  const [newCatName, setNewCatName] = useState('');
  const [newSubName, setNewSubName] = useState('');

  useEffect(() => {
    getCategories().then(r => setCats(r.data)).catch(() => {});
  }, []);

  const topLevel = cats.filter(c => !c.parent_id);
  const selectedCatObj = topLevel.find(c => c.name === category);
  const subLevel = selectedCatObj
    ? cats.filter(c => c.parent_id === selectedCatObj.id)
    : [];

  async function handleAddCategory() {
    if (!newCatName.trim()) return;
    try {
      await addCategory({ name: newCatName.trim(), parent_id: null });
      const r = await getCategories();
      setCats(r.data);
      onChange({ category: newCatName.trim(), subcategory: null });
    } catch {}
    setNewCatName('');
    setAddingCat(false);
  }

  async function handleAddSubcategory() {
    if (!newSubName.trim() || !selectedCatObj) return;
    try {
      await addCategory({ name: newSubName.trim(), parent_id: selectedCatObj.id });
      const r = await getCategories();
      setCats(r.data);
      onChange({ category, subcategory: newSubName.trim() });
    } catch {}
    setNewSubName('');
    setAddingSub(false);
  }

  function handleCatChange(e) {
    const val = e.target.value;
    if (val === '__add__') { setAddingCat(true); return; }
    onChange({ category: val || null, subcategory: null });
  }

  function handleSubChange(e) {
    const val = e.target.value;
    if (val === '__add__') { setAddingSub(true); return; }
    onChange({ category, subcategory: val || null });
  }

  return (
    <div className="category-combobox">
      {addingCat ? (
        <div className="category-combobox__adder">
          <input
            className="form-input"
            value={newCatName}
            onChange={e => setNewCatName(e.target.value)}
            placeholder="New category name"
            onKeyDown={e => { if (e.key === 'Enter') handleAddCategory(); if (e.key === 'Escape') setAddingCat(false); }}
            autoFocus
          />
          <button className="btn btn--sm" onClick={handleAddCategory}>Add</button>
          <button className="btn btn--sm btn--ghost" onClick={() => setAddingCat(false)}>Cancel</button>
        </div>
      ) : (
        <select className="form-select" value={category ?? ''} onChange={handleCatChange}>
          <option value="">— no category —</option>
          {topLevel.map(c => (
            <option key={c.id} value={c.name}>{c.name}</option>
          ))}
          <option value="__add__">+ Add new category…</option>
        </select>
      )}

      {category && !addingCat && (
        addingSub ? (
          <div className="category-combobox__adder">
            <input
              className="form-input"
              value={newSubName}
              onChange={e => setNewSubName(e.target.value)}
              placeholder="New subcategory name"
              onKeyDown={e => { if (e.key === 'Enter') handleAddSubcategory(); if (e.key === 'Escape') setAddingSub(false); }}
              autoFocus
            />
            <button className="btn btn--sm" onClick={handleAddSubcategory}>Add</button>
            <button className="btn btn--sm btn--ghost" onClick={() => setAddingSub(false)}>Cancel</button>
          </div>
        ) : (
          <select className="form-select" value={subcategory ?? ''} onChange={handleSubChange}>
            <option value="">— no subcategory —</option>
            {subLevel.map(c => (
              <option key={c.id} value={c.name}>{c.name}</option>
            ))}
            <option value="__add__">+ Add new subcategory…</option>
          </select>
        )
      )}
    </div>
  );
}
