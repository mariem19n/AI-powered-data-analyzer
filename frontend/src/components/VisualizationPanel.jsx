import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

/**
 * VisualizationPanel — affiche une liste de figures Plotly.
 * Plotly direct, sans react-plotly.js (voir ChartCard.jsx pour le pourquoi).
 */

function normalizeFigure(figure) {
  if (!figure || typeof figure !== "object") return null;
  const data = Array.isArray(figure.data) ? figure.data : [];
  const layout =
    figure.layout && typeof figure.layout === "object" ? figure.layout : {};
  if (!data.length) return null;
  return { data, layout };
}

function PlotlyFigure({ figure }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const layout = {
      autosize: true,
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 48, r: 24, t: 36, b: 46, ...(figure.layout?.margin || {}) },
      ...figure.layout,
    };

    const config = { displayModeBar: false, responsive: true };

    Plotly.newPlot(containerRef.current, figure.data, layout, config);

    const node = containerRef.current;
    return () => {
      if (node) Plotly.purge(node);
    };
  }, [figure]);

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
  );
}

export default function VisualizationPanel({ visualizations, error }) {
  if (!Array.isArray(visualizations) || visualizations.length === 0) {
    if (error) {
      return (
        <section className="panel visualization-panel">
          <p className="error-note">{error}</p>
          <div className="chart-empty muted-state">
            <strong>Aucune visualisation disponible</strong>
          </div>
        </section>
      );
    }
    return null;
  }

  const figures = visualizations.map(normalizeFigure).filter(Boolean);

  return (
    <section className="panel visualization-panel">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Visualizations</span>
          <h2>Dynamic charts</h2>
        </div>
        <span className="panel-badge">
          {figures.length} chart{figures.length === 1 ? "" : "s"}
        </span>
      </div>

      {error ? <p className="error-note">{error}</p> : null}

      {figures.length ? (
        <div className="plot-list">
          {figures.map((figure, index) => (
            <div className="plot-frame" key={`plot-${index}`}>
              <PlotlyFigure figure={figure} />
            </div>
          ))}
        </div>
      ) : (
        <div className="chart-empty muted-state">
          <strong>Aucune visualisation disponible</strong>
          <p>
            Charts will appear here when the analysis includes Plotly
            visualizations.
          </p>
        </div>
      )}
    </section>
  );
}