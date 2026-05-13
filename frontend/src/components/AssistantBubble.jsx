import { useState } from "react";
import KPICard from "./KPICard.jsx";
import ChartCard from "./ChartCard.jsx";
import InsightsList from "./InsightsList.jsx";
import ClarificationChips from "./ClarificationChips.jsx";
import DetailsDrawer from "./DetailsDrawer.jsx";
import {
  computeMarketSummary,
  extractRecords,
  formatCurrency,
  formatPercent,
} from "../utils/marketSummary.js";

export default function AssistantBubble({ response, isError, errorMessage, onSubmit }) {
  const [showDetails, setShowDetails] = useState(false);

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

  const insights = Array.isArray(response?.insights) ? response.insights.filter(Boolean) : [];
  const recommendations = Array.isArray(response?.recommendations)
    ? response.recommendations.filter(Boolean)
    : [];
  const visualizations = Array.isArray(response?.visualizations)
    ? response.visualizations.filter((v) => v && typeof v === "object")
    : [];

  const needsClarification = Boolean(response?.needs_clarification);
  const isExternal = response?.response_mode === "external";
  const sourceUrl =
    response?.external_data?.url ??
    response?.external_data?.source ??
    null;

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

        {summary && (
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

        {isExternal && sourceUrl && (
          <a
            href={sourceUrl}
            className="source-link"
            target="_blank"
            rel="noopener noreferrer"
          >
            View source →
          </a>
        )}

        <div className="bubble-footer">
          <button
            type="button"
            className="details-btn"
            onClick={() => setShowDetails((v) => !v)}
          >
            {showDetails ? "Hide details" : "Details"}
          </button>
        </div>

        {showDetails && <DetailsDrawer response={response} />}
      </div>
    </div>
  );
}
