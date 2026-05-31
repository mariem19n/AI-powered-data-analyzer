const ANOMALIES = [
  { crypto: "BTC", type: "volume", date: "2026-05-27", severity: "high" },
  { crypto: "ETH", type: "price", date: "2026-05-25", severity: "medium" },
  { crypto: "SOL", type: "volume", date: "2026-05-24", severity: "medium" },
  { crypto: "XRP", type: "price", date: "2026-05-22", severity: "low" },
];

export default function RecentAnomalies({ items = ANOMALIES }) {
  return (
    <section className="dashboard-panel">
      <div className="dashboard-section-header compact">
        <div>
          <span>Recent Anomalies</span>
          <h2>Latest signals</h2>
        </div>
      </div>
      <div className="anomaly-feed">
        {items.map((item) => (
          <article className="anomaly-item" key={`${item.crypto}-${item.type}-${item.date}`}>
            <span className={`severity-dot ${item.severity}`} aria-hidden="true" />
            <div>
              <strong>{item.crypto} {item.type}</strong>
              <small>{item.date}</small>
            </div>
            <em className={`severity-pill ${item.severity}`}>{item.severity}</em>
          </article>
        ))}
      </div>
    </section>
  );
}
