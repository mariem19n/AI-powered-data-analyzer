const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

function formatPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return currencyFormatter.format(n);
}

function formatVolume(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return compactCurrencyFormatter.format(n);
}

function formatChange(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0.00%";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function changeClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "neutral";
  return n > 0 ? "positive" : "negative";
}

function Sparkline({ points = [], trend = 0 }) {
  const values = points.length ? points : [18, 22, 20, 26, 24, 30, 28];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const path = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 100;
      const y = 34 - ((value - min) / range) * 28;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg className={`sparkline ${trend < 0 ? "negative" : "positive"}`} viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

export default function CryptoCard({ asset, onClick }) {
  const name = asset.name || asset.symbol;
  const sparkline = asset.sparkline || [];

  return (
    <button type="button" className="dashboard-crypto-card" onClick={onClick}>
      <div className="dashboard-crypto-top">
        <span>
          <strong>{asset.symbol}</strong>
          <small>{name}</small>
        </span>
        <em className={`crypto-change ${changeClass(asset.change_24h_pct)}`}>
          {formatChange(asset.change_24h_pct)}
        </em>
      </div>

      <div className="dashboard-crypto-price">{formatPrice(asset.price_usd)}</div>
      <Sparkline points={sparkline} trend={Number(asset.change_24h_pct) || 0} />

      <dl className="dashboard-crypto-meta">
        <div>
          <dt>Volume</dt>
          <dd>{formatVolume(asset.volume)}</dd>
        </div>
        <div>
          <dt>Signal</dt>
          <dd>{Number(asset.change_24h_pct) >= 0 ? "Risk-on" : "Cooling"}</dd>
        </div>
      </dl>
    </button>
  );
}
