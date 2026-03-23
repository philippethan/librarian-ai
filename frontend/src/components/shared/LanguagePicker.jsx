import { useState, useRef, useEffect } from 'react';

const LANGUAGES = [
  ['af','Afrikaans'],['ar','Arabic'],['az','Azerbaijani'],['be','Belarusian'],
  ['bg','Bulgarian'],['bn','Bengali'],['bs','Bosnian'],['ca','Catalan'],
  ['cs','Czech'],['cy','Welsh'],['da','Danish'],['de','German'],
  ['el','Greek'],['en','English'],['eo','Esperanto'],['es','Spanish'],
  ['et','Estonian'],['eu','Basque'],['fa','Persian'],['fi','Finnish'],
  ['fr','French'],['ga','Irish'],['gl','Galician'],['gu','Gujarati'],
  ['he','Hebrew'],['hi','Hindi'],['hr','Croatian'],['hu','Hungarian'],
  ['hy','Armenian'],['id','Indonesian'],['is','Icelandic'],['it','Italian'],
  ['ja','Japanese'],['ka','Georgian'],['kk','Kazakh'],['km','Khmer'],
  ['ko','Korean'],['lt','Lithuanian'],['lv','Latvian'],['mk','Macedonian'],
  ['ml','Malayalam'],['mn','Mongolian'],['ms','Malay'],['mt','Maltese'],
  ['nl','Dutch'],['no','Norwegian'],['pl','Polish'],['pt','Portuguese'],
  ['ro','Romanian'],['ru','Russian'],['sk','Slovak'],['sl','Slovenian'],
  ['sq','Albanian'],['sr','Serbian'],['sv','Swedish'],['sw','Swahili'],
  ['ta','Tamil'],['te','Telugu'],['th','Thai'],['tr','Turkish'],
  ['uk','Ukrainian'],['ur','Urdu'],['uz','Uzbek'],['vi','Vietnamese'],
  ['zh','Chinese'],
];

export default function LanguagePicker({ value, onChange }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  const selectedLang = LANGUAGES.find(([code]) => code === value);
  const displayValue = selectedLang ? `${selectedLang[0]} - ${selectedLang[1]}` : '';

  const filtered = query.trim()
    ? LANGUAGES.filter(([code, name]) =>
        code.includes(query.toLowerCase()) ||
        name.toLowerCase().includes(query.toLowerCase())
      )
    : LANGUAGES;

  useEffect(() => {
    function handleClick(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
        setQuery('');
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  function handleSelect(code) {
    onChange(code || null);
    setOpen(false);
    setQuery('');
  }

  function handleInputChange(e) {
    setQuery(e.target.value);
    setOpen(true);
  }

  return (
    <div className="lang-picker" ref={containerRef}>
      <input
        className="form-input"
        value={open ? query : displayValue}
        onChange={handleInputChange}
        onFocus={() => { setOpen(true); setQuery(''); }}
        placeholder="Search language…"
      />
      {open && (
        <ul className="lang-picker__dropdown">
          <li className="lang-picker__option" onMouseDown={() => handleSelect(null)}>
            — none —
          </li>
          {filtered.map(([code, name]) => (
            <li
              key={code}
              className={`lang-picker__option${value === code ? ' lang-picker__option--active' : ''}`}
              onMouseDown={() => handleSelect(code)}
            >
              {code} - {name}
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="lang-picker__empty">No results</li>
          )}
        </ul>
      )}
    </div>
  );
}
