const MACRO_DATA = [
  { name: "Federal Funds Rate", value: "5.25%", change: "stable", direction: "neutral" },
  { name: "CPI", value: "3.4%", change: "+0.1 pt", direction: "up" },
  { name: "Unemployment", value: "3.9%", change: "-0.1 pt", direction: "down" },
  { name: "DXY", value: "104.2", change: "+0.3%", direction: "up" },
  { name: "10Y Treasury", value: "4.45%", change: "-0.04 pt", direction: "down" },
];

function arrow(direction) {
  if (direction === "up") return "↑";
  if (direction === "down") return "↓";
  return "→";
}

export default function MacroPulse({ indicators = MACRO_DATA }) {
  return (
    <section className="dashboard-panel">
      <div className="dashboard-section-header compact">
        <div>
          <span>Macro Pulse</span>
          <h2>FRED indicators</h2>
        </div>
      </div>
      <div className="macro-list">
        {indicators.map((item) => (
          <div className="macro-row" key={item.name}>
            <span className={`macro-direction ${item.direction}`}>{arrow(item.direction)}</span>
            <span className="macro-name">{item.name}</span>
            <strong>{item.value}</strong>
            <small>{item.change}</small>
          </div>
        ))}
      </div>
    </section>
  );
}
