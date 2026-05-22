export default function ForecastUncertaintyBadge({ badge }) {
  if (!badge) return null;

  return (
    <div className={`forecast-uncertainty-badge ${badge.level}`} role="status">
      <span>{badge.label}</span>
      <strong>{badge.value}</strong>
    </div>
  );
}
