export default function ExpertStatCard({ label, value, tone = "neutral" }) {
  return (
    <article className={`expert-stat-card ${tone}`}>
      <span>{label}</span>
      <strong>{value ?? "-"}</strong>
    </article>
  );
}
