export default function InsightsList({ insights, label = "Key insights" }) {
  const items = Array.isArray(insights) ? insights.filter(Boolean) : [];
  if (!items.length) return null;

  return (
    <div className="insights-list">
      <span className="insights-label">{label}</span>
      <ul>
        {items.slice(0, 4).map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
