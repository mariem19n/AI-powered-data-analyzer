export default function DataUsedPanel({ summary, records }) {
  const hasRecords = Array.isArray(records) && records.length > 0;

  return (
    <section className="panel stack-panel">
      <div className="panel-heading compact">
        <div>
          <span className="section-kicker">Data used</span>
          <h2>Data used for this analysis</h2>
        </div>
      </div>

      {summary ? (
        <dl className="data-list">
          <div>
            <dt>Symbol</dt>
            <dd>{summary.symbol || "Detected market asset"}</dd>
          </div>
          <div>
            <dt>Period</dt>
            <dd>{summary.dateRange.label}</dd>
          </div>
          <div>
            <dt>Observations</dt>
            <dd>{summary.observations}</dd>
          </div>
          <div>
            <dt>Metric</dt>
            <dd>{summary.metric}</dd>
          </div>
          <div>
            <dt>Data source</dt>
            <dd>Cryptocurrency daily prices</dd>
          </div>
        </dl>
      ) : (
        <p className="muted-state">
          {hasRecords
            ? "The returned data could not be summarized as a price series."
            : "No analysis data loaded yet."}
        </p>
      )}
    </section>
  );
}
