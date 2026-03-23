const OPTIONS = ['Beginner', 'Intermediate', 'Advanced'];

export default function SegmentedControl({ value, onChange }) {
  return (
    <div className="segmented-control">
      {OPTIONS.map(opt => (
        <button
          key={opt}
          type="button"
          className="segmented-control__btn"
          style={value === opt ? {
            background: 'var(--accent)',
            color: '#fff',
            borderColor: 'var(--accent)',
          } : {}}
          onClick={() => onChange(value === opt ? null : opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}
