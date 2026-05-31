const SENTIMENT_DATA = [
  { symbol: "BTC", score: 2.3 },
  { symbol: "ETH", score: -0.8 },
  { symbol: "SOL", score: 1.2 },
  { symbol: "BNB", score: 0.4 },
];

function label(score) {
  if (score > 1) return "bullish";
  if (score < -1) return "bearish";
  return "neutral";
}

export default function SentimentRadar({ items = SENTIMENT_DATA }) {
  return (
    <section className="dashboard-panel">
      <div className="dashboard-section-header compact">
        <div>
          <span>Sentiment Radar</span>
          <h2>7-day GDELT tone</h2>
        </div>
      </div>
      <div className="sentiment-list">
        {items.map((item) => {
          const width = Math.min(Math.abs(item.score) / 3, 1) * 50;
          return (
            <div className="sentiment-row" key={item.symbol}>
              <strong>{item.symbol}</strong>
              <div className="sentiment-bar" aria-hidden="true">
                <span
                  className={item.score >= 0 ? "positive" : "negative"}
                  style={{
                    width: `${width}%`,
                    left: item.score >= 0 ? "50%" : `${50 - width}%`,
                  }}
                />
              </div>
              <em>{item.score > 0 ? "+" : ""}{item.score.toFixed(1)} {label(item.score)}</em>
            </div>
          );
        })}
      </div>
    </section>
  );
}
