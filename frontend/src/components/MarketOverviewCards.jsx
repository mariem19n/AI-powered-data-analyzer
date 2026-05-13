import { formatCurrency, formatPercent } from "../utils/marketSummary.js";

function Sparkline({ points }) {
  if (!Array.isArray(points) || points.length < 2) return null;

  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const path = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100;
      const y = 38 - ((value - min) / range) * 32;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg className="sparkline" viewBox="0 0 100 42" preserveAspectRatio="none">
      <path d={path} />
    </svg>
  );
}

function MetricCard({ label, value, accent, meta, children }) {
  return (
    <article className={`metric-card ${accent || ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {meta ? <small>{meta}</small> : null}
      {children}
    </article>
  );
}

export default function MarketOverviewCards({ summary }) {
  if (!summary) {
    return (
      <div className="market-empty">
        <p>Ask a question to generate market insights.</p>
      </div>
    );
  }

  return (
    <div className="market-grid">
      <MetricCard
        label="Latest Price"
        value={formatCurrency(summary.latestPrice)}
        accent="blue"
        meta={summary.symbol || "Market"}
      />
      <MetricCard
        label="Monthly Change"
        value={formatPercent(summary.variationPercent)}
        accent={summary.trend === "up" ? "olive" : "pink"}
        meta={summary.trend === "up" ? "Uptrend" : "Downtrend"}
      />
      <MetricCard
        label="Average Price"
        value={formatCurrency(summary.averagePrice)}
        accent="purple"
        meta={`${summary.observations} observations`}
      />
      <MetricCard
        label="Price Range"
        value={`${formatCurrency(summary.minPrice)} - ${formatCurrency(summary.maxPrice)}`}
        accent="pink"
        meta={summary.dateRange.label}
      />
      <MetricCard
        label="Trend Direction"
        value={summary.trend === "up" ? "Up" : "Down"}
        accent={summary.trend === "up" ? "olive" : "pink"}
        meta="Based on first and latest price"
      >
        <Sparkline points={summary.sparkline} />
      </MetricCard>
    </div>
  );
}
