export default function ExternalSourceCard({ source, compact = false }) {
  if (!source?.url || !source?.title) return null;

  return (
    <a
      className={`external-source-card ${compact ? "compact" : ""}`}
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      <div className="external-source-card-top">
        <strong>{source.title}</strong>
        {source.domain ? <span>{source.domain}</span> : null}
      </div>
      {source.snippet ? <p>{source.snippet}</p> : null}
      <span className="external-source-card-link">Ouvrir la source ↗</span>
    </a>
  );
}
