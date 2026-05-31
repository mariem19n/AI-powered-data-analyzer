import { useEffect, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";

/**
 * ChartCard — Plotly direct, sans react-plotly.js.
 *
 * react-plotly.js produit "Element type is invalid" sur certains setups
 * (React 19, Vite + plotly.js-dist-min sans default export propre).
 * On utilise Plotly.newPlot directement, c'est l'API officielle stable.
 */
function MaximizeIcon() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="16"
      viewBox="0 0 24 24"
      width="16"
    >
      <path
        d="M15 3h6v6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M9 21H3v-6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M21 3l-7 7"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M3 21l7-7"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="20"
      viewBox="0 0 24 24"
      width="20"
    >
      <path
        d="M18 6 6 18"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="m6 6 12 12"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function stripHtml(value) {
  return String(value || "")
    .replace(/<[^>]*>/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function extractChartHeading(figure) {
  const explicitTitle = figure?.title || figure?.layout?.title_text;
  const explicitSubtitle = figure?.subtitle || figure?.layout?.subtitle;
  const rawTitle = explicitTitle ?? figure?.layout?.title?.text ?? figure?.layout?.title ?? "";

  if (typeof rawTitle !== "string") {
    return {
      title: stripHtml(explicitTitle),
      subtitle: stripHtml(explicitSubtitle),
    };
  }

  const parts = rawTitle.split(/<br\s*\/?>/i);
  return {
    title: stripHtml(parts[0]),
    subtitle: stripHtml(explicitSubtitle || parts.slice(1).join(" ")),
  };
}

function ExpandedChartModal({ figure, title, subtitle, onClose }) {
  const modalChartRef = useRef(null);

  useEffect(() => {
    function handleKeyDown(event) {
      if (event.key === "Escape") onClose();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!modalChartRef.current) return;

    const layout = {
      ...(figure.layout || {}),
      autosize: true,
      height: undefined,
      width: undefined,
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      title: undefined,
    };
    const config = {
      responsive: true,
      displaylogo: false,
    };

    Plotly.newPlot(modalChartRef.current, figure.data || [], layout, config);

    const node = modalChartRef.current;
    return () => {
      if (node) Plotly.purge(node);
    };
  }, [figure]);

  return (
    <div className="chart-modal-backdrop" role="presentation">
      <div
        aria-label={title || "Visualisation"}
        aria-modal="true"
        className="chart-modal"
        role="dialog"
      >
        <div className="chart-modal-header">
          <div>
            <p className="chart-modal-kicker">Visualisation</p>
            <h2>{title || "Visualisation"}</h2>
            {subtitle ? <p className="chart-subtitle">{subtitle}</p> : null}
          </div>

          <button
            type="button"
            className="chart-modal-close"
            onClick={onClose}
            title="Fermer"
            aria-label="Fermer"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="chart-modal-body">
          <div
            ref={modalChartRef}
            className="chart-modal-plot"
          />
        </div>
      </div>
    </div>
  );
}

export default function ChartCard({ figure, onExpand }) {
  const containerRef = useRef(null);
  const [isExpanded, setIsExpanded] = useState(false);

  const hasData =
    figure && Array.isArray(figure.data) && figure.data.length > 0;

  const { title, subtitle } = extractChartHeading(figure);

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
      <div className="chart-header">
        {title ? (
          <div>
            <h4 className="chart-title">{title}</h4>
            {subtitle ? <p className="chart-subtitle">{subtitle}</p> : null}
          </div>
        ) : (
          <span />
        )}
        <button
          type="button"
          className="chart-expand-btn"
          onClick={() => {
            onExpand?.();
            setIsExpanded(true);
          }}
          title="Agrandir la visualisation"
          aria-label="Agrandir la visualisation"
        >
          <MaximizeIcon />
        </button>
      </div>
      <div className="chart-frame">
        <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      </div>
      {isExpanded && (
        <ExpandedChartModal
          figure={figure}
          title={title}
          subtitle={subtitle}
          onClose={() => setIsExpanded(false)}
        />
      )}
    </div>
  );
}
