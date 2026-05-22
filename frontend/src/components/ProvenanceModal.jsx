import { useEffect } from "react";
import ExternalSourceCard from "./ExternalSourceCard.jsx";
import {
  getAnalysisMethodLabel,
  getExternalMethodLabel,
  getExternalQuery,
  getExternalSources,
  getInternalDataSources,
  getPlanTasks,
  getResponseMode,
  getResponseModeLabel,
  hasExternalSources,
} from "../utils/externalSources.js";

function CloseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M18 6 6 18M6 6l12 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function formatValue(value) {
  if (value == null || value === "") return "";
  if (typeof value === "number" && Number.isFinite(value)) {
    return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 }).format(value);
  }
  if (typeof value === "boolean") return value ? "Oui" : "Non";
  if (Array.isArray(value)) return value.filter(Boolean).join(", ");
  return String(value);
}

function isScalar(value) {
  return value == null || ["string", "number", "boolean"].includes(typeof value);
}

function getSqlItems(response) {
  return getInternalDataSources(response).filter((item) => item?.sql);
}

function getColumnSummary(item) {
  if (!Array.isArray(item?.columns) || item.columns.length === 0) return "";
  return item.columns.slice(0, 10).join(", ");
}

function getRowsSummary(response) {
  const rows = getInternalDataSources(response)
    .map((item) => item?.row_count)
    .filter((value) => value != null);

  if (rows.length === 0) return "";
  return rows.reduce((total, value) => total + Number(value || 0), 0);
}

function getProvenanceDataSources(response) {
  return response?.provenance?.data_sources || [];
}

function getProvenanceMethods(response) {
  return response?.provenance?.methods || [];
}

function pickStatsFacts(response) {
  const stats = response?.analysis_stats || {};
  const facts = [];

  Object.entries(stats).forEach(([stepId, item]) => {
    if (!item || typeof item !== "object") return;
    const candidates = {
      model_used: item.model_used ?? item.model,
      forecast_horizon: item.forecast_horizon ?? item.metadata?.horizon_days,
      mae: item.mae ?? item.evaluation?.mae,
      rmse: item.rmse ?? item.evaluation?.rmse,
      mape: item.mape ?? item.evaluation?.mape,
      backtesting_windows: item.backtesting_windows ?? item.evaluation?.n_cutoffs,
      anomaly_count: item.anomaly_count ?? item.n_anomalies ?? item.anomalies_count,
      anomaly_rate: item.anomaly_rate,
      algorithm: item.algorithm ?? item.method ?? item.metadata?.algorithm,
      correlation: item.correlation ?? item.pearson ?? item.spearman,
      mean: item.mean,
      min: item.min,
      max: item.max,
      median: item.median,
      std: item.std,
      trend: item.trend ?? item.trend_direction ?? item.diagnostics?.trend_direction,
    };

    Object.entries(candidates).forEach(([key, value]) => {
      if (isScalar(value) && formatValue(value)) {
        facts.push({ stepId, key, value: formatValue(value) });
      }
    });
  });

  return facts.slice(0, 12);
}

function InfoCard({ label, value, tone = "method" }) {
  if (!value) return null;

  return (
    <article className={`provenance-info-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function Section({ title, count, children }) {
  return (
    <section className="provenance-source-section">
      <div className="provenance-source-heading">
        <h3>{title}</h3>
        {count > 0 ? <span>{count}</span> : null}
      </div>
      {children}
    </section>
  );
}

export default function ProvenanceModal({ response, onClose }) {
  const responseMode = getResponseMode(response);
  const modeLabel = getResponseModeLabel(response);
  const externalSources = getExternalSources(response);
  const externalQuery = getExternalQuery(response);
  const showExternal = (responseMode === "external" || responseMode === "hybrid") && hasExternalSources(response);
  const showExternalQuery = (responseMode === "external" || responseMode === "hybrid") && externalQuery;
  const showInternal = responseMode === "internal" || responseMode === "hybrid";
  const internalData = getInternalDataSources(response);
  const provenanceData = getProvenanceDataSources(response);
  const sqlItems = getSqlItems(response);
  const rowsSummary = getRowsSummary(response);
  const planTasks = getPlanTasks(response);
  const provenanceMethods = getProvenanceMethods(response);
  const statsFacts = pickStatsFacts(response);
  const internalMethodLabel = getAnalysisMethodLabel(response);

  useEffect(() => {
    document.body.style.overflow = "hidden";

    function handleKeyDown(event) {
      if (event.key === "Escape") onClose();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    <div className="provenance-modal-backdrop" onClick={onClose}>
      <section
        className="provenance-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="provenance-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="provenance-modal-header">
          <div>
            <span className="provenance-kicker">Sources et méthode</span>
            <h2 id="provenance-title">Comment cette réponse a été construite</h2>
          </div>
          <button
            type="button"
            className="provenance-modal-close"
            onClick={onClose}
            aria-label="Fermer"
          >
            <CloseIcon />
          </button>
        </header>

        <div className="provenance-modal-content">
          <span className="provenance-mode-badge">{modeLabel}</span>

          <div className="provenance-info-grid">
            <InfoCard label="Mode de réponse" value={modeLabel} tone="mode" />
            {showInternal && (
              <InfoCard label="Méthode utilisée" value={internalMethodLabel} tone="method" />
            )}
            {showExternal && responseMode === "external" && (
              <InfoCard label="Méthode utilisée" value={getExternalMethodLabel()} tone="method" />
            )}
            {showExternal && responseMode === "hybrid" && (
              <InfoCard label="Enrichissement externe" value={getExternalMethodLabel()} tone="query" />
            )}
            {showExternalQuery && (
              <InfoCard label="Recherche Tavily" value={externalQuery} tone="query" />
            )}
            {showInternal && rowsSummary !== "" && (
              <InfoCard label="Lignes analysées" value={`${rowsSummary}`} tone="data" />
            )}
            {showInternal && planTasks.length > 0 && (
              <InfoCard label="Tâche d’analyse" value={planTasks.join(", ")} tone="data" />
            )}
          </div>

          {showInternal && provenanceMethods.length > 0 && (
            <Section title="Méthodes internes" count={provenanceMethods.length}>
              <div className="provenance-method-list">
                {provenanceMethods.map((method, index) => (
                  <article className="provenance-method-card" key={`${method.name}-${index}`}>
                    <strong>{method.name}</strong>
                    {method.description ? <p>{method.description}</p> : null}
                    {method.algorithm ? <span>{method.algorithm}</span> : null}
                  </article>
                ))}
              </div>
            </Section>
          )}

          {showInternal && (internalData.length > 0 || provenanceData.length > 0) && (
            <Section title="Données utilisées" count={internalData.length || provenanceData.length}>
              <div className="provenance-method-list">
                {internalData.map((item, index) => (
                  <article className="provenance-method-card" key={`data-${index}`}>
                    <strong>{item.step_id || `Jeu de données ${index + 1}`}</strong>
                    {item.row_count != null ? <p>{item.row_count} lignes retournées</p> : null}
                    {getColumnSummary(item) ? <span>Colonnes: {getColumnSummary(item)}</span> : null}
                  </article>
                ))}
                {provenanceData.map((item, index) => (
                  <article className="provenance-method-card" key={`prov-data-${index}`}>
                    <strong>{item.name}</strong>
                    {item.description ? <p>{item.description}</p> : null}
                    {item.record_count != null ? <span>{item.record_count} lignes</span> : null}
                    {Array.isArray(item.tables) && item.tables.length > 0 ? (
                      <span>Tables: {item.tables.join(", ")}</span>
                    ) : null}
                  </article>
                ))}
              </div>
            </Section>
          )}

          {showInternal && sqlItems.length > 0 && (
            <Section title="SQL généré" count={sqlItems.length}>
              <div className="provenance-sql-list">
                {sqlItems.map((item, index) => (
                  <pre className="provenance-sql-card" key={`sql-${index}`}>
                    <code>{item.sql}</code>
                  </pre>
                ))}
              </div>
            </Section>
          )}

          {showInternal && statsFacts.length > 0 && (
            <Section title="Statistiques calculées" count={statsFacts.length}>
              <div className="provenance-stat-grid">
                {statsFacts.map((fact, index) => (
                  <div className="provenance-stat-card" key={`${fact.stepId}-${fact.key}-${index}`}>
                    <span>{fact.key.replace(/_/g, " ")}</span>
                    <strong>{fact.value}</strong>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {showExternal && (
            <Section title={responseMode === "hybrid" ? "Sources d’enrichissement" : "Sources externes"} count={externalSources.length}>
              <div className="provenance-source-list">
                {externalSources.map((source, index) => (
                  <ExternalSourceCard
                    key={`${source.url}-${index}`}
                    source={source}
                  />
                ))}
              </div>
            </Section>
          )}

          {showExternal && (
            <aside className="provenance-disclaimer">
              Information provenant de sources web externes via Tavily. Vérifiez les sources citées
              pour confirmer l’exactitude.
            </aside>
          )}
        </div>
      </section>
    </div>
  );
}
