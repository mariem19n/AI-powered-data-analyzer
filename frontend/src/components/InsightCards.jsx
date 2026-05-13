export default function InsightCards({ insights }) {
  return (
    <section className="panel stack-panel">
      <div className="panel-heading compact">
        <div>
          <span className="section-kicker">Insights</span>
          <h2>AI insights</h2>
        </div>
      </div>
      {insights.length ? (
        <div className="card-list">
          {insights.map((insight, index) => (
            <article className="text-card" key={`${insight}-${index}`}>
              <span className="pill">AI Insight</span>
              <p>{insight}</p>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted-state">No insights generated yet.</p>
      )}
    </section>
  );
}
