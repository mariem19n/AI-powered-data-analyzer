import { getAnalysisSteps, toTitleCase } from "./analysisStats.js";

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatCurrencyOrNA(value) {
  const number = toNumber(value);
  if (number == null) return "N/A";

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: number >= 100 ? 0 : 2,
  }).format(number);
}

function formatPercentOrNA(value) {
  const number = toNumber(value);
  if (number == null) return "N/A";

  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(2)}%`;
}

function formatDaysOrNA(value) {
  const number = toNumber(value);
  if (number == null) return "N/A";

  return `${new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0,
  }).format(number)} days`;
}

function isForecastStep(step) {
  const model = String(step.model ?? step.metadata?.model ?? "").toLowerCase();

  return (
    model === "prophet" ||
    step.metadata?.horizon_days != null ||
    step.forecast.length > 0
  );
}

function getForecastYhat(point) {
  return toNumber(
    point?.yhat ??
      point?.forecast ??
      point?.forecast_value ??
      point?.predicted ??
      point?.value
  );
}

function getLastForecastValue(step) {
  const diagnosticValue = toNumber(step.diagnostics?.last_forecast_value);
  if (diagnosticValue != null) return diagnosticValue;

  for (let index = step.forecast.length - 1; index >= 0; index -= 1) {
    const value = getForecastYhat(step.forecast[index]);
    if (value != null) return value;
  }

  return null;
}

function getAverageForecastValue(step) {
  const explicitValue = toNumber(
    step.diagnostics?.average_forecast_value ??
      step.metadata?.average_forecast_value ??
      step.evaluation?.average_forecast_value
  );
  if (explicitValue != null) return explicitValue;

  const values = step.forecast
    .map((point) => getForecastYhat(point))
    .filter((value) => value != null);

  if (!values.length) return null;

  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function getForecastStep(response) {
  return getAnalysisSteps(response?.analysis_stats).find(isForecastStep) ?? null;
}

function getForecastStepWithUncertainty(response) {
  return (
    getAnalysisSteps(response?.analysis_stats).find(
      (step) =>
        isForecastStep(step) &&
        toNumber(step.diagnostics?.mean_ci_width_pct) != null
    ) ?? null
  );
}

export function buildForecastSummaryCards(response, marketSummary) {
  const step = getForecastStep(response);
  if (!step) return null;

  const currentPrice =
    toNumber(step.diagnostics?.last_historical_value) ??
    toNumber(marketSummary?.latestPrice);
  const forecastEnd = getLastForecastValue(step);
  const expectedChange = toNumber(step.diagnostics?.forecast_vs_history_change_pct);
  const averageForecast = getAverageForecastValue(step);
  const model = step.model ?? step.metadata?.model;

  return [
    {
      label: "Current Price",
      value: formatCurrencyOrNA(currentPrice),
    },
    {
      label: "Forecast End",
      value: formatCurrencyOrNA(forecastEnd),
    },
    {
      label: "Expected Change",
      value: formatPercentOrNA(expectedChange),
      positive: expectedChange == null ? undefined : expectedChange >= 0,
    },
    {
      label: "Average Forecast",
      value: formatCurrencyOrNA(averageForecast),
    },
    {
      label: "Horizon",
      value: formatDaysOrNA(step.metadata?.horizon_days),
    },
    {
      label: "Model",
      value: toTitleCase(model),
    },
  ];
}

export function buildForecastUncertaintyBadge(response) {
  const step = getForecastStepWithUncertainty(response);
  if (!step) return null;

  const uncertainty = toNumber(step.diagnostics?.mean_ci_width_pct);
  if (uncertainty == null || uncertainty < 10) return null;

  if (uncertainty >= 25) {
    return {
      level: "high",
      label: "Incertitude élevée - prévision à interpréter avec prudence",
      value: formatPercentOrNA(uncertainty),
    };
  }

  return {
    level: "moderate",
    label: "Incertitude modérée",
    value: formatPercentOrNA(uncertainty),
  };
}
