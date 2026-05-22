export function isPlainObject(value) {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

export function toTitleCase(value) {
  if (value == null || value === "") return "N/A";
  return String(value)
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function formatAnalysisValue(value, options = {}) {
  if (value == null || value === "") return "N/A";

  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    const formatted = new Intl.NumberFormat("en-US", {
      maximumFractionDigits: options.maximumFractionDigits ?? 2,
    }).format(value);

    return options.suffix ? `${formatted}${options.suffix}` : formatted;
  }

  return String(value);
}

export function getAnalysisSteps(analysisStats) {
  if (!isPlainObject(analysisStats)) return [];

  return Object.entries(analysisStats)
    .filter(([, step]) => isPlainObject(step))
    .map(([stepId, step]) => ({
      stepId,
      forecast: Array.isArray(step.forecast) ? step.forecast : [],
      model: step.model || step.model_used,
      evaluation: {
        ...(isPlainObject(step.evaluation) ? step.evaluation : {}),
        mae: step.evaluation?.mae ?? step.mae,
        rmse: step.evaluation?.rmse ?? step.rmse,
        mape: step.evaluation?.mape ?? step.mape,
        backtesting_windows:
          step.evaluation?.backtesting_windows ?? step.backtesting_windows,
      },
      metadata: {
        ...(isPlainObject(step.metadata) ? step.metadata : {}),
        forecast_horizon: step.metadata?.forecast_horizon ?? step.forecast_horizon,
        horizon_days: step.metadata?.horizon_days ?? step.forecast_horizon,
        model_used: step.metadata?.model_used ?? step.model_used,
      },
      diagnostics: isPlainObject(step.diagnostics) ? step.diagnostics : {},
    }));
}

export function hasAnalysisStats(analysisStats) {
  return getAnalysisSteps(analysisStats).length > 0;
}
