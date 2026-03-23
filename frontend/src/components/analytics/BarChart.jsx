// Horizontal bar chart — plain SVG, no charting library
// Props: data=[{label, count}], title, color

const LABEL_W = 160;
const BAR_MAX_W = 220;
const VALUE_W = 40;
const PAD_X = 12;
const BAR_H = 24;
const GAP = 6;
const PAD_Y = 12;
const VIEW_W = PAD_X + LABEL_W + BAR_MAX_W + VALUE_W + PAD_X;

export default function BarChart({ data, title, color = 'var(--chart-1)' }) {
  if (!data || data.length === 0) {
    return (
      <div className="an-chart-wrapper">
        <h3 className="an-chart-title">{title}</h3>
        <p className="an-chart-empty">No data available.</p>
      </div>
    );
  }

  const items = data.slice(0, 15);
  const max = Math.max(...items.map(d => d.count), 1);
  const viewH = PAD_Y + items.length * (BAR_H + GAP) - GAP + PAD_Y;

  return (
    <div className="an-chart-wrapper">
      <h3 className="an-chart-title">{title}</h3>
      <svg
        viewBox={`0 0 ${VIEW_W} ${viewH}`}
        width="100%"
        style={{ display: 'block' }}
        aria-label={title}
      >
        {items.map((d, i) => {
          const y = PAD_Y + i * (BAR_H + GAP);
          const barW = (d.count / max) * BAR_MAX_W;
          const barX = PAD_X + LABEL_W;
          return (
            <g key={d.label ?? i}>
              <text
                x={PAD_X + LABEL_W - 8}
                y={y + BAR_H / 2 + 4}
                textAnchor="end"
                fontSize="12"
                fill="var(--text-muted)"
              >
                {(d.label ?? '—').length > 22
                  ? (d.label ?? '—').slice(0, 21) + '…'
                  : (d.label ?? '—')}
              </text>
              <rect
                x={barX}
                y={y}
                width={Math.max(barW, 2)}
                height={BAR_H}
                rx={3}
                fill={color}
                opacity="0.85"
              />
              <text
                x={barX + barW + 6}
                y={y + BAR_H / 2 + 4}
                fontSize="12"
                fill="var(--text-primary)"
              >
                {d.count}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
