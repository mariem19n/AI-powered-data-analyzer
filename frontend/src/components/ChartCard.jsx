import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

/**
 * ChartCard — Plotly direct, sans react-plotly.js.
 *
 * react-plotly.js produit "Element type is invalid" sur certains setups
 * (React 19, Vite + plotly.js-dist-min sans default export propre).
 * On utilise Plotly.newPlot directement, c'est l'API officielle stable.
 */
export default function ChartCard({ figure }) {
  const containerRef = useRef(null);

  const hasData =
    figure && Array.isArray(figure.data) && figure.data.length > 0;

  const rawTitle = figure?.layout?.title?.text ?? figure?.layout?.title ?? "";
  const title = typeof rawTitle === "string" ? rawTitle : "";

  useEffect(() => {
    if (!hasData || !containerRef.current) return;

    const layout = {
      autosize: true,
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 48, r: 20, t: 16, b: 46 },
      font: {
        family: "Inter, ui-sans-serif, sans-serif",
        size: 11,
        color: "#6B7280",
      },
      xaxis: {
        showgrid: false,
        linecolor: "#E5E7EB",
        tickfont: { size: 11 },
      },
      yaxis: {
        showgrid: true,
        gridcolor: "#F1F5F9",
        linecolor: "#E5E7EB",
        tickfont: { size: 11 },
      },
      colorway: ["#5580b9", "#8d9323", "#e14480", "#6c367e", "#16a34a"],
      legend: { font: { size: 11 } },
      ...figure.layout,
      // Le titre est rendu en HTML au-dessus, pas par Plotly
      title: undefined,
    };

    const config = { displayModeBar: false, responsive: true };

    Plotly.newPlot(containerRef.current, figure.data, layout, config);

    const node = containerRef.current;
    return () => {
      if (node) Plotly.purge(node);
    };
  }, [figure, hasData]);

  if (!hasData) return null;

  return (
    <div className="chart-card">
      {title && (
        <div className="chart-header">
          <h4 className="chart-title">{title}</h4>
        </div>
      )}
      <div className="chart-frame">
        <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      </div>
    </div>
  );
}