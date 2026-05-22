import { useEffect } from "react";
import {
  formatAnalysisValue,
  getAnalysisSteps,
  toTitleCase,
} from "../utils/analysisStats.js";

function getStepTitle(step) {
  const model = toTitleCase(step.model);
  const suffix = model !== "N/A" ? `${model} Forecasting` : "Model Evaluation";
  return `${step.stepId} - ${suffix}`;
}

function MetricRow({ label, value }) {
  if (value == null || value === "" || value === "N/A") return null;

  return (
    <div className="analysis-metric-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function getMetricRows(step) {
  const skipped = step.evaluation.skipped;
  const rows = [
    ["Model used", toTitleCase(step.model || step.metadata.model_used)],
    [
      "Forecast horizon",
      formatAnalysisValue(step.metadata.horizon_days ?? step.metadata.forecast_horizon, {
        suffix: " days",
      }),
    ],
    [
      "Historical points",
      formatAnalysisValue(step.metadata.n_historical, {
        maximumFractionDigits: 0,
      }),
    ],
    ["MAE", formatAnalysisValue(step.evaluation.mae)],
    ["MAPE", formatAnalysisValue(step.evaluation.mape)],
    ["RMSE", formatAnalysisValue(step.evaluation.rmse)],
    [
      "Backtesting windows",
      formatAnalysisValue(step.evaluation.n_cutoffs ?? step.evaluation.backtesting_windows, {
        maximumFractionDigits: 0,
      }),
    ],
    ["Evaluation skipped", skipped === true ? "Yes" : ""],
    ["Skip reason", skipped === true ? formatAnalysisValue(step.evaluation.skip_reason) : ""],
    ["Trend direction", toTitleCase(step.diagnostics.trend_direction)],
    [
      "Mean uncertainty",
      formatAnalysisValue(step.diagnostics.mean_ci_width_pct, {
        suffix: "%",
      }),
    ],
  ];

  return rows.filter(([, value]) => value && value !== "N/A");
}

export default function AnalysisStatsModal({ analysisStats, onClose }) {
  const steps = getAnalysisSteps(analysisStats);

  useEffect(() => {
    function handleKeyDown(event) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="analysis-modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="analysis-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="analysis-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="analysis-modal-header">
          <div>
            <span className="analysis-modal-kicker">Technical evaluation</span>
            <h2 id="analysis-modal-title">Model evaluation</h2>
          </div>
          <button
            type="button"
            className="analysis-modal-close"
            aria-label="Close model evaluation"
            onClick={onClose}
          >
            x
          </button>
        </div>

        <div className="analysis-modal-content">
          {steps.length === 0 && (
            <p className="analysis-empty-message">
              Aucune évaluation de modèle disponible pour cette réponse.
            </p>
          )}

          {steps.map((step) => {
            const metricRows = getMetricRows(step);

            return (
              <section className="analysis-step-card" key={step.stepId}>
                <h3>{getStepTitle(step)}</h3>
                {metricRows.length > 0 ? (
                  <div className="analysis-metric-grid">
                    {metricRows.map(([label, value]) => (
                      <MetricRow key={label} label={label} value={value} />
                    ))}
                  </div>
                ) : (
                  <p className="analysis-empty-message">
                    Aucune évaluation de modèle disponible pour cette réponse.
                  </p>
                )}
              </section>
            );
          })}
        </div>

        <div className="analysis-modal-actions">
          <button type="button" className="analysis-modal-secondary" onClick={onClose}>
            Fermer
          </button>
        </div>
      </div>
    </div>
  );
}
