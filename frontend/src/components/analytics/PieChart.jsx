// Donut chart — plain SVG, no charting library
// Props: data=[{label, count}], title

const COLORS = [
  'var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)',
  'var(--chart-5)', 'var(--chart-6)', 'var(--chart-7)', 'var(--chart-8)',
];

const CX = 110;
const CY = 110;
const R = 88;
const INNER_R = 52;
const VIEW_W = 460;
const VIEW_H = 220;

function slicePath(cx, cy, r, innerR, startAngle, endAngle) {
  const toRad = a => (a * Math.PI) / 180;
  const cos1 = Math.cos(toRad(startAngle));
  const sin1 = Math.sin(toRad(startAngle));
  const cos2 = Math.cos(toRad(endAngle));
  const sin2 = Math.sin(toRad(endAngle));
  const large = endAngle - startAngle > 180 ? 1 : 0;
  return [
    `M ${cx + r * cos1} ${cy + r * sin1}`,
    `A ${r} ${r} 0 ${large} 1 ${cx + r * cos2} ${cy + r * sin2}`,
    `L ${cx + innerR * cos2} ${cy + innerR * sin2}`,
    `A ${innerR} ${innerR} 0 ${large} 0 ${cx + innerR * cos1} ${cy + innerR * sin1}`,
    'Z',
  ].join(' ');
}

export default function PieChart({ data, title }) {
  if (!data || data.length === 0) {
    return (
      <div className="an-chart-wrapper">
        <h3 className="an-chart-title">{title}</h3>
        <p className="an-chart-empty">No data available.</p>
      </div>
    );
  }

  const total = data.reduce((s, d) => s + d.count, 0) || 1;
  let angle = -90; // start at top

  const slices = data.map((d, i) => {
    const sweep = (d.count / total) * 360;
    const start = angle;
    angle += sweep;
    return { ...d, start, end: angle, color: COLORS[i % COLORS.length] };
  });

  return (
    <div className="an-chart-wrapper">
      <h3 className="an-chart-title">{title}</h3>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        width="100%"
        style={{ display: 'block' }}
        aria-label={title}
      >
        {/* Donut slices */}
        {slices.map((s, i) => {
          // Handle full circle (single slice) — arc command won't draw a full circle
          if (s.end - s.start >= 359.99) {
            return (
              <g key={i}>
                <circle cx={CX} cy={CY} r={R} fill={s.color} opacity="0.85" />
                <circle cx={CX} cy={CY} r={INNER_R} fill="var(--bg-surface)" />
              </g>
            );
          }
          return (
            <path
              key={i}
              d={slicePath(CX, CY, R, INNER_R, s.start, s.end)}
              fill={s.color}
              opacity="0.85"
            />
          );
        })}

        {/* Center total */}
        <text x={CX} y={CY - 6} textAnchor="middle" fontSize="22" fontWeight="600" fill="var(--text-primary)">
          {total}
        </text>
        <text x={CX} y={CY + 12} textAnchor="middle" fontSize="11" fill="var(--text-muted)">
          total
        </text>

        {/* Legend */}
        {slices.map((s, i) => {
          const legendX = 240;
          const legendY = 18 + i * 22;
          if (legendY > VIEW_H - 8) return null;
          const pct = ((s.count / total) * 100).toFixed(1);
          return (
            <g key={i}>
              <rect x={legendX} y={legendY} width={12} height={12} rx={2} fill={s.color} opacity="0.85" />
              <text x={legendX + 18} y={legendY + 10} fontSize="12" fill="var(--text-muted)">
                {(s.label ?? '—').length > 18
                  ? (s.label ?? '—').slice(0, 17) + '…'
                  : s.label ?? '—'}
                {' '}({pct}%)
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
