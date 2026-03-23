import { useEffect, useState } from 'react';
import { getAnalytics } from '../../api/analytics';
import BarChart from './BarChart';
import PieChart from './PieChart';
import './AnalyticsPage.css';

// ── Line / area chart ─────────────────────────────────────────────────────────

const LC_W = 560;
const LC_H = 200;
const LC_PAD = { top: 20, right: 20, bottom: 36, left: 44 };
const LC_INNER_W = LC_W - LC_PAD.left - LC_PAD.right;
const LC_INNER_H = LC_H - LC_PAD.top - LC_PAD.bottom;

function LineChart({ data, title }) {
  if (!data || data.length === 0) {
    return (
      <div className="an-chart-wrapper">
        <h3 className="an-chart-title">{title}</h3>
        <p className="an-chart-empty">No data available.</p>
      </div>
    );
  }

  const maxCount = Math.max(...data.map(d => d.count), 1);
  const n = data.length;

  const pts = data.map((d, i) => ({
    x: LC_PAD.left + (n === 1 ? LC_INNER_W / 2 : (i / (n - 1)) * LC_INNER_W),
    y: LC_PAD.top + LC_INNER_H - (d.count / maxCount) * LC_INNER_H,
    count: d.count,
    month: d.month,
  }));

  const linePath = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
  const areaPath = `${linePath} L ${pts[pts.length - 1].x} ${LC_PAD.top + LC_INNER_H} L ${pts[0].x} ${LC_PAD.top + LC_INNER_H} Z`;

  // Y-axis ticks: 0, 25%, 50%, 75%, 100%
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(t => ({
    y: LC_PAD.top + LC_INNER_H - t * LC_INNER_H,
    label: Math.round(t * maxCount),
  }));

  // X-axis labels: show at most 6, evenly spaced
  const step = Math.ceil(n / 6);
  const xLabels = pts.filter((_, i) => i % step === 0 || i === n - 1);

  return (
    <div className="an-chart-wrapper">
      <h3 className="an-chart-title">{title}</h3>
      <svg viewBox={`0 0 ${LC_W} ${LC_H}`} width="100%" style={{ display: 'block' }} aria-label={title}>
        {/* Grid lines */}
        {yTicks.map(t => (
          <line
            key={t.label}
            x1={LC_PAD.left}
            y1={t.y}
            x2={LC_W - LC_PAD.right}
            y2={t.y}
            stroke="var(--border)"
            strokeWidth="0.75"
          />
        ))}

        {/* Y-axis labels */}
        {yTicks.map(t => (
          <text
            key={t.label}
            x={LC_PAD.left - 6}
            y={t.y + 4}
            textAnchor="end"
            fontSize="10"
            fill="var(--text-muted)"
          >
            {t.label}
          </text>
        ))}

        {/* Area fill */}
        <path d={areaPath} fill="var(--chart-1)" fillOpacity="0.15" />

        {/* Line */}
        <path d={linePath} fill="none" stroke="var(--chart-1)" strokeWidth="2" strokeLinejoin="round" />

        {/* Data points */}
        {pts.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r="4" fill="var(--chart-1)" />
        ))}

        {/* X-axis labels */}
        {xLabels.map((p, i) => (
          <text
            key={i}
            x={p.x}
            y={LC_PAD.top + LC_INNER_H + 18}
            textAnchor="middle"
            fontSize="10"
            fill="var(--text-muted)"
          >
            {p.month}
          </text>
        ))}
      </svg>
    </div>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, accent }) {
  return (
    <div className="an-stat-card" style={accent ? { borderTopColor: accent } : {}}>
      <div className="an-stat-card__value">{value ?? 0}</div>
      <div className="an-stat-card__label">{label}</div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const CONF_COLORS = [
  'var(--conf-low)',
  'var(--conf-mid)',
  'var(--chart-7)',
  'var(--chart-2)',
  'var(--conf-high)',
];

export default function AnalyticsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    getAnalytics()
      .then(r => setData(r.data))
      .catch(() => setError('Failed to load analytics.'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="an-page an-page--loading">Loading analytics…</div>;
  if (error)   return <div className="an-page an-page--error">{error}</div>;
  if (!data)   return null;

  const rs = data.reading_status_counts ?? {};
  const hist = data.confidence_histogram ?? [];

  return (
    <div className="an-page">
      {/* Totals row */}
      <div className="an-stat-row">
        <StatCard label="Total books" value={data.total_books} accent="var(--accent)" />
        <StatCard label="Total shelves" value={data.total_shelves} accent="var(--chart-6)" />
      </div>

      {/* Reading status */}
      <h2 className="an-section-title">Reading status</h2>
      <div className="an-stat-row">
        <StatCard label="To read"  value={rs['to-read']} accent="var(--badge-reading-to-read-text)" />
        <StatCard label="Reading"  value={rs.reading}    accent="var(--badge-reading-reading-text)" />
        <StatCard label="Done"     value={rs.done}       accent="var(--conf-high)" />
        <StatCard label="Not set"  value={rs.unset}      accent="var(--text-muted)" />
      </div>

      {/* Confidence histogram */}
      <h2 className="an-section-title">Confidence distribution</h2>
      <div className="an-stat-row">
        {['0.0-0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0'].map((bucket, i) => {
          const entry = hist.find(h => h.bucket === bucket);
          return (
            <StatCard
              key={bucket}
              label={bucket}
              value={entry?.count ?? 0}
              accent={CONF_COLORS[i]}
            />
          );
        })}
      </div>

      {/* Charts */}
      <div className="an-charts-grid">
        <BarChart
          data={data.books_per_category}
          title="Books by category (top 15)"
          color="var(--chart-1)"
        />
        <BarChart
          data={data.books_per_author}
          title="Books by author (top 15)"
          color="var(--chart-3)"
        />
        <PieChart
          data={data.language_distribution}
          title="Language distribution"
        />
        <LineChart
          data={data.books_added_per_month}
          title="Books added per month (last 12 months)"
        />
      </div>
    </div>
  );
}
