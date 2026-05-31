import { Fragment } from "react";

const SYMBOLS = ["BTC", "ETH", "SOL", "BNB"];
const MATRIX = [
  [1, 0.82, 0.68, 0.55],
  [0.82, 1, 0.71, 0.49],
  [0.68, 0.71, 1, 0.44],
  [0.55, 0.49, 0.44, 1],
];

function color(value) {
  const intensity = Math.round(45 + Math.abs(value) * 160);
  if (value >= 0) return `rgba(85, 128, 185, ${intensity / 220})`;
  return `rgba(225, 68, 128, ${intensity / 220})`;
}

export default function CorrelationMini({ symbols = SYMBOLS, matrix = MATRIX }) {
  return (
    <section className="dashboard-panel">
      <div className="dashboard-section-header compact">
        <div>
          <span>Correlation Mini</span>
          <h2>30-day crypto matrix</h2>
        </div>
      </div>
      <div className="correlation-grid" style={{ "--corr-size": symbols.length + 1 }}>
        <span />
        {symbols.map((symbol) => <strong key={`h-${symbol}`}>{symbol}</strong>)}
        {symbols.map((rowSymbol, rowIndex) => (
          <Fragment key={`row-${rowSymbol}`}>
            <strong key={`r-${rowSymbol}`}>{rowSymbol}</strong>
            {symbols.map((colSymbol, colIndex) => {
              const value = matrix[rowIndex]?.[colIndex] ?? 0;
              return (
                <span
                  className="correlation-cell"
                  key={`${rowSymbol}-${colSymbol}`}
                  style={{ background: color(value) }}
                  title={`${rowSymbol}/${colSymbol}: ${value.toFixed(2)}`}
                >
                  {value.toFixed(2)}
                </span>
              );
            })}
          </Fragment>
        ))}
      </div>
    </section>
  );
}
