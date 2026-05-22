import { useState } from "react";
import KPICard from "./KPICard.jsx";
import ChartCard from "./ChartCard.jsx";
import InsightsList from "./InsightsList.jsx";
import ClarificationChips from "./ClarificationChips.jsx";
import DetailsDrawer from "./DetailsDrawer.jsx";
import AnalysisStatsModal from "./AnalysisStatsModal.jsx";
import ForecastUncertaintyBadge from "./ForecastUncertaintyBadge.jsx";
import ProvenanceModal from "./ProvenanceModal.jsx";
import ExternalSourceCard from "./ExternalSourceCard.jsx";
import {
  computeMarketSummary,
  extractRecords,
  formatCurrency,
  formatPercent,
} from "../utils/marketSummary.js";
import {
  buildForecastSummaryCards,
  buildForecastUncertaintyBadge,
} from "../utils/forecastSummary.js";
import {
  getExternalSources,
  getResponseMode,
  shouldShowDetails,
  shouldShowModelEvaluation,
  shouldShowProvenance,
} from "../utils/externalSources.js";

export default function AssistantBubble({ response, isError, errorMessage, onSubmit }) {
  const [showDetails, setShowDetails] = useState(false);
  const [showAnalysisStats, setShowAnalysisStats] = useState(false);
  const [showProvenance, setShowProvenance] = useState(false);

  if (isError) {
    return (
      <div className="message-row assistant">
        <div className="assistant-bubble error-bubble">
          <p className="bubble-text">{errorMessage}</p>
        </div>
      </div>
    );
  }

  const records = extractRecords(response);
  const summary = computeMarketSummary(records);
  const forecastSummaryCards = buildForecastSummaryCards(response, summary);
  const forecastUncertaintyBadge = buildForecastUncertaintyBadge(response);

  const insights = Array.isArray(response?.insights) ? response.insights.filter(Boolean) : [];
  const recommendations = Array.isArray(response?.recommendations)
    ? response.recommendations.filter(Boolean)
    : [];
  const visualizations = Array.isArray(response?.visualizations)
    ? response.visualizations.filter((v) => v && typeof v === "object")
    : [];

  const needsClarification = Boolean(response?.needs_clarification);
  const hasModelEvaluation = shouldShowModelEvaluation(response);
  const externalSources = getExternalSources(response);
  const hasProvenance = shouldShowProvenance(response);
  const hasDetails = shouldShowDetails(response);
  const responseMode = getResponseMode(response);
  const isExternal = responseMode === "external" || responseMode === "hybrid";
  const visibleExternalSources = isExternal ? externalSources : [];

  const mainText =
    insights[0] ??
    (records.length
      ? "Here's what I found based on your question."
      : "I could not find enough data to generate a full analysis.");
  const extraInsights = insights.slice(1, 4);

  const clarificationQuestion = response?.clarification_question ?? null;
  const clarificationSuggestions = Array.isArray(response?.clarification_suggestions)
    ? response.clarification_suggestions
    : [];

  return (
    <div className="message-row assistant">
      <div className="assistant-bubble">
        {isExternal && <span className="web-badge">Web</span>}

        <p className="bubble-text">{mainText}</p>

        {forecastUncertaintyBadge && (
          <ForecastUncertaintyBadge badge={forecastUncertaintyBadge} />
        )}

        {forecastSummaryCards ? (
          <div className="kpi-row">
            {forecastSummaryCards.map((card) => (
              <KPICard
                key={card.label}
                label={card.label}
                value={card.value}
                positive={card.positive}
              />
            ))}
          </div>
        ) : summary && (
          <div className="kpi-row">
            <KPICard
              label="Price"
              value={formatCurrency(summary.latestPrice)}
              symbol={summary.symbol ?? undefined}
            />
            <KPICard
              label="Change"
              value={formatPercent(summary.variationPercent)}
              positive={
                summary.variationPercent == null
                  ? undefined
                  : summary.variationPercent >= 0
              }
            />
            <KPICard
              label="Average"
              value={formatCurrency(summary.averagePrice)}
            />
          </div>
        )}

        {visualizations.length > 0 && (
          <ChartCard figure={visualizations[0]} />
        )}

        {extraInsights.length > 0 && (
          <InsightsList insights={extraInsights} label="Key insights" />
        )}

        {recommendations.length > 0 && (
          <InsightsList insights={recommendations} label="Recommendations" />
        )}

        {needsClarification && (
          <ClarificationChips
            question={clarificationQuestion}
            suggestions={clarificationSuggestions}
            onSelect={onSubmit}
          />
        )}

        {visibleExternalSources.length > 0 && (
          <div className="external-source-preview">
            <div className="external-source-preview-heading">
              <span>Sources externes</span>
              <strong>{visibleExternalSources.length}</strong>
            </div>
            <div className="external-source-grid">
              {visibleExternalSources.slice(0, 5).map((src, i) => (
                <ExternalSourceCard key={`${src.url}-${i}`} source={src} compact />
              ))}
            </div>
          </div>
        )}

        <div className="bubble-footer">
          {hasModelEvaluation && (
            <button
              type="button"
              className="model-evaluation-btn"
              onClick={() => setShowAnalysisStats(true)}
            >
              Show model evaluation
            </button>
          )}
          {hasProvenance && (
            <button
              type="button"
              className="provenance-btn"
              onClick={() => setShowProvenance(true)}
            >
              Sources et méthode
            </button>
          )}
          {hasDetails && (
            <button
              type="button"
              className="details-btn"
              onClick={() => setShowDetails((v) => !v)}
            >
              {showDetails ? "Hide details" : "Details"}
            </button>
          )}
        </div>

        {showDetails && <DetailsDrawer response={response} />}
        {showAnalysisStats && (
          <AnalysisStatsModal
            analysisStats={response?.analysis_stats}
            onClose={() => setShowAnalysisStats(false)}
          />
        )}
        {showProvenance && (
          <ProvenanceModal
            response={response}
            onClose={() => setShowProvenance(false)}
          />
        )}
      </div>
    </div>
  );
}
