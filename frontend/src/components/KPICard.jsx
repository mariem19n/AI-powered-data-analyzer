export default function KPICard({ label, value, symbol, positive }) {
  const valueClass =
    positive === true
      ? "kpi-value positive"
      : positive === false
      ? "kpi-value negative"
      : "kpi-value";

  return (
    <div className="kpi-card">
      <span className="kpi-label">{label}</span>
      <strong className={valueClass}>{value}</strong>
      {symbol ? <span className="kpi-symbol">{symbol}</span> : null}
    </div>
  );
}
