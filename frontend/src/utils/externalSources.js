export function getIntent(response) {
  return response?.intent?.primary || "";
}

export function getPlanTasks(response) {
  return response?.plan?.steps?.map((step) => step?.instruction?.task).filter(Boolean) || [];
}

export function getResponseMode(response) {
  return response?.provenance?.response_mode || response?.response_mode || "internal";
}

export function getResponseModeLabel(response) {
  const mode = getResponseMode(response);

  if (mode === "external") return "Sources externes";
  if (mode === "hybrid") return "Données internes + sources externes";
  return "Données internes";
}

export function getExternalSources(response) {
  const direct = response?.external_data?.sources || [];
  const fromProvenance =
    response?.provenance?.external_sources?.flatMap((group) => group.sources || []) || [];

  const sources = direct.length > 0 ? direct : fromProvenance;

  return sources.filter((source) => source?.url && source?.title);
}

export function hasExternalSources(response) {
  return getExternalSources(response).length > 0;
}

export function getInternalDataSources(response) {
  return response?.data || [];
}

export function getExternalQuery(response) {
  return (
    response?.external_data?.query ||
    response?.provenance?.external_sources?.find((group) => group?.query)?.query ||
    ""
  );
}

export function getExternalMethodLabel() {
  return "Synthèse externe via Tavily";
}

export function getPrimaryTask(response) {
  const intent = getIntent(response);
  const tasks = getPlanTasks(response);

  if (intent === "external_knowledge") return "external_knowledge";
  if (intent) return intent;
  return tasks[0] || "";
}

export function getAnalysisMethodLabel(response) {
  const task = getPrimaryTask(response);
  const tasks = getPlanTasks(response);

  if (task === "aggregation") return "Agrégation SQL";
  if (task === "descriptive") return "Analyse descriptive";
  if (task === "anomaly_detection") return "Détection d’anomalies";
  if (task === "correlation") return "Analyse de corrélation";
  if (task === "comparison") return "Analyse comparative";
  if (task === "forecasting" || tasks.includes("forecasting")) return "Prévision";
  if (task === "external_knowledge" || task === "external_summary") {
    return "Synthèse externe via Tavily";
  }
  if (tasks.includes("hybrid_summary")) return "Analyse interne avec enrichissement externe";
  return "Analyse interne";
}

function hasForecastStatsItem(item) {
  if (!item || typeof item !== "object") return false;

  const evaluation = item.evaluation || {};
  const metadata = item.metadata || {};
  const diagnostics = item.diagnostics || {};
  const hasValue = (value) => value != null && value !== "";

  return Boolean(
    hasValue(item.model_used) ||
      hasValue(item.forecast_horizon) ||
      hasValue(item.mae) ||
      hasValue(item.rmse) ||
      hasValue(item.mape) ||
      hasValue(item.backtesting_windows) ||
      item.model === "prophet" ||
      hasValue(evaluation.mae) ||
      hasValue(evaluation.rmse) ||
      hasValue(evaluation.mape) ||
      hasValue(evaluation.n_cutoffs) ||
      hasValue(metadata.horizon_days) ||
      hasValue(metadata.forecast_horizon) ||
      hasValue(diagnostics.mean_ci_width_pct)
  );
}

export function hasForecastEvaluation(response) {
  const intent = getIntent(response);
  const tasks = getPlanTasks(response);
  const stats = response?.analysis_stats || {};
  const flatStats = Object.values(stats || {}).filter(Boolean);
  const hasForecastStats = flatStats.some(hasForecastStatsItem);

  return intent === "forecasting" || tasks.includes("forecasting") || hasForecastStats;
}

export function shouldShowModelEvaluation(response) {
  return hasForecastEvaluation(response);
}

export function hasGeneratedSql(response) {
  return getInternalDataSources(response).some((item) => Boolean(item?.sql));
}

export function hasAnalysisStatsPayload(response) {
  return Object.values(response?.analysis_stats || {}).some(
    (item) => item && typeof item === "object" && Object.keys(item).length > 0
  );
}

export function shouldShowDetails(response) {
  const hasSqlData = getInternalDataSources(response).some(
    (item) =>
      item?.sql ||
      item?.row_count != null ||
      (Array.isArray(item?.columns) && item.columns.length > 0)
  );
  const hasWarnings = Array.isArray(response?.warnings) && response.warnings.length > 0;
  const hasPlanSteps = Array.isArray(response?.plan?.steps) && response.plan.steps.length > 0;

  return hasSqlData || hasWarnings || hasPlanSteps || hasAnalysisStatsPayload(response);
}

export function shouldShowProvenance(response) {
  return Boolean(
    response?.provenance ||
      hasExternalSources(response) ||
      getInternalDataSources(response).length > 0 ||
      getIntent(response) ||
      getPlanTasks(response).length > 0 ||
      hasAnalysisStatsPayload(response)
  );
}
