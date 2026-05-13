export default function RecommendationCards({ recommendations }) {
  return (
    <section className="panel stack-panel">
      <div className="panel-heading compact">
        <div>
          <span className="section-kicker">Next steps</span>
          <h2>Recommendations</h2>
        </div>
      </div>
      {recommendations.length ? (
        <div className="card-list">
          {recommendations.map((recommendation, index) => (
            <article className="text-card recommendation" key={`${recommendation}-${index}`}>
              <span className="pill">Recommendation</span>
              <p>{recommendation}</p>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted-state">Recommendations will appear after a successful analysis.</p>
      )}
    </section>
  );
}
