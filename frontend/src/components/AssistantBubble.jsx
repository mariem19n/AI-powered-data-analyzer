import { useEffect, useRef, useState } from "react";
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
import {
  exportResponseAsMarkdown,
  exportResponseAsPdf,
  hasExportableResponse,
} from "../utils/exportReport.js";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

function FileTextIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="14" viewBox="0 0 24 24" width="14">
      <path
        d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M14 2v6h6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path d="M8 13h8" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
      <path d="M8 17h6" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
    </svg>
  );
}

function FileDownloadIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="14" viewBox="0 0 24 24" width="14">
      <path
        d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M14 2v6h6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M12 12v6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2"
      />
      <path
        d="m9 15 3 3 3-3"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="13" viewBox="0 0 24 24" width="13">
      <path
        d="m6 9 6 6 6-6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function ThumbsUpIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="15" viewBox="0 0 24 24" width="15">
      <path
        d="M7 10v12"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 7a2 2 0 0 1-2 1.7H7"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M7 10H4a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M14 10V5.5a2.5 2.5 0 0 0-5 0V10"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function ThumbsDownIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="15" viewBox="0 0 24 24" width="15">
      <path
        d="M17 14V2"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M9 18.1 10 14H4.2a2 2 0 0 1-2-2.3l1.4-7A2 2 0 0 1 5.6 3H17"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M17 2h3a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-3"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M10 14v4.5a2.5 2.5 0 0 0 5 0V14"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function MessageCircleIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="15" viewBox="0 0 24 24" width="15">
      <path
        d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="14" viewBox="0 0 24 24" width="14">
      <path
        d="m22 2-7 20-4-9-9-4Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M22 2 11 13"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function XIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="14" viewBox="0 0 24 24" width="14">
      <path
        d="M18 6 6 18"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2"
      />
      <path
        d="m6 6 12 12"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function ExportMenu({ response, originalQuestion, onExport }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    function handlePointerDown(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  function handleExport(format) {
    setOpen(false);
    onExport?.(format);
    if (format === "markdown") {
      exportResponseAsMarkdown(response, originalQuestion);
      return;
    }
    exportResponseAsPdf(response, originalQuestion);
  }

  return (
    <div className="export-menu" ref={menuRef}>
      <button
        type="button"
        className="export-menu-trigger"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        title="Exporter"
      >
        <FileDownloadIcon />
        <span>Exporter</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <div className="export-menu-panel" role="menu">
          <button
            type="button"
            role="menuitem"
            onClick={() => handleExport("markdown")}
          >
            <FileTextIcon />
            Markdown
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => handleExport("pdf")}
          >
            <FileDownloadIcon />
            PDF
          </button>
        </div>
      )}
    </div>
  );
}

function getIntentName(response) {
  const intent = response?.intent;
  if (!intent) return "";
  if (typeof intent === "string") return intent;
  return intent.primary || "";
}

function getResponseTask(response) {
  return (
    response?.task ||
    response?.metadata?.task ||
    response?.analysis_task ||
    response?.analysis_stats?.analysis_type ||
    response?.analysis_stats?.metadata?.task ||
    ""
  );
}

function getResponseCharCount(response) {
  const parts = [];
  if (Array.isArray(response?.insights)) parts.push(...response.insights);
  if (Array.isArray(response?.recommendations)) parts.push(...response.recommendations);
  if (typeof response?.answer === "string") parts.push(response.answer);
  if (typeof response?.summary === "string") parts.push(response.summary);
  const text = parts.filter(Boolean).join("\n");
  if (text) return text.length;
  try {
    return JSON.stringify(response || {}).length;
  } catch {
    return 0;
  }
}

function detectCopyZone(target) {
  const element = target instanceof Element ? target : null;
  if (!element) return null;
  if (element.closest("pre, code")) return "sql";
  if (element.closest(".chart-card, .chart-modal")) return "visualization";
  if (element.closest(".details-drawer, .analysis-modal")) return "data";
  if (element.closest(".external-source-preview, .provenance-modal")) return "source";
  if (element.closest(".insights-list, .bubble-text")) return "insight";
  return null;
}

function FeedbackWidget({
  response,
  originalQuestion,
  conversationId,
  messageId,
  getImplicitSignals,
}) {
  const [selectedFeedback, setSelectedFeedback] = useState(null);
  const [commentMode, setCommentMode] = useState(null);
  const [isCommentOpen, setIsCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sentMessage, setSentMessage] = useState("");
  const [error, setError] = useState("");

  async function submitFeedback(feedbackType) {
    if (isSending) return;
    setIsSending(true);
    setError("");

    const responseCompositeId =
      response?.session_id && response?.created_at
        ? `${response.session_id}-${response.created_at}`
        : null;
    const responseId =
      response?.response_id ||
      messageId ||
      response?.message_id ||
      response?.messageId ||
      response?.session_id ||
      responseCompositeId ||
      `${Date.now()}`;
    const rating =
      feedbackType === "positive" ? 5 : feedbackType === "negative" ? 1 : 3;

    try {
      const apiResponse = await fetch(`${API_BASE_URL}/api/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId || response?.conversation_id || null,
          message_id: messageId || response?.message_id || response?.messageId || null,
          response_id: responseId,
          session_id: response?.session_id || localStorage.getItem("session_id") || null,
          question: originalQuestion || response?.question || "",
          intent: getIntentName(response),
          rating,
          feedback_type: feedbackType,
          comment: comment.trim() || null,
          implicit_signals: getImplicitSignals?.() || null,
          response_snapshot: response,
        }),
      });

      if (!apiResponse.ok) {
        throw new Error("feedback_failed");
      }

      setSelectedFeedback(feedbackType === "comment" ? null : feedbackType);
      setIsCommentOpen(false);
      setSentMessage(
        feedbackType === "negative"
          ? "Merci, votre feedback a \u00e9t\u00e9 enregistr\u00e9."
          : "Merci pour votre feedback"
      );
    } catch {
      setError("Impossible d'envoyer le feedback.");
    } finally {
      setIsSending(false);
    }
  }

  function openNegativeFeedback() {
    setSelectedFeedback("negative");
    setCommentMode("negative");
    setIsCommentOpen(true);
    setSentMessage("");
    setError("");
  }

  function openCommentFeedback() {
    setCommentMode("comment");
    setIsCommentOpen(true);
    setSentMessage("");
    setError("");
  }

  function cancelComment() {
    if (commentMode === "negative" && !sentMessage) {
      setSelectedFeedback(null);
    }
    setIsCommentOpen(false);
    setCommentMode(null);
    setError("");
  }

  const placeholder =
    commentMode === "negative"
      ? "Qu'est-ce qui ne va pas dans cette r\u00e9ponse ?"
      : "Ajoutez un commentaire sur cette r\u00e9ponse...";

  return (
    <div className={`feedback-widget${sentMessage ? " sent" : ""}`}>
      <div className="feedback-row">
        <span className="feedback-label">Votre avis</span>
        <div
          className="feedback-actions"
          aria-label={"Donner un feedback sur la r\u00e9ponse"}
        >
          <button
            type="button"
            className={`feedback-icon-btn positive${
              selectedFeedback === "positive" ? " active" : ""
            }`}
            onClick={() => submitFeedback("positive")}
            disabled={isSending}
            title={"R\u00e9ponse utile"}
            aria-label={"R\u00e9ponse utile"}
          >
            <ThumbsUpIcon />
          </button>
          <button
            type="button"
            className={`feedback-icon-btn negative${
              selectedFeedback === "negative" ? " active" : ""
            }`}
            onClick={openNegativeFeedback}
            disabled={isSending}
            title={"R\u00e9ponse non utile"}
            aria-label={"R\u00e9ponse non utile"}
          >
            <ThumbsDownIcon />
          </button>
        </div>
      </div>

      {sentMessage && <p className="feedback-success">{sentMessage}</p>}

      {isCommentOpen && (
        <div className="feedback-form">
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder={placeholder}
            rows={2}
            disabled={isSending}
          />
          <div className="feedback-form-actions">
            <button
              type="button"
              className="feedback-submit-btn"
              onClick={() => submitFeedback(commentMode || "comment")}
              disabled={isSending}
            >
              <SendIcon />
              {isSending ? "Envoi..." : "Envoyer"}
            </button>
            <button
              type="button"
              className="feedback-cancel-btn"
              onClick={cancelComment}
              disabled={isSending}
            >
              <XIcon />
              Annuler
            </button>
          </div>
        </div>
      )}

      {error && <p className="feedback-error">{error}</p>}

      <button
        type="button"
        className="feedback-comment-float"
        onClick={openCommentFeedback}
        disabled={isSending}
        title="Ajouter un commentaire"
        aria-label="Ajouter un commentaire"
      >
        <MessageCircleIcon />
      </button>
    </div>
  );
}

export default function AssistantBubble({
  response,
  isError,
  errorMessage,
  onSubmit,
  originalQuestion,
  conversationId,
  messageId,
}) {
  const [showDetails, setShowDetails] = useState(false);
  const [showAnalysisStats, setShowAnalysisStats] = useState(false);
  const [showProvenance, setShowProvenance] = useState(false);
  const renderedAtRef = useRef(Date.now());
  const [behaviorSignals, setBehaviorSignals] = useState({
    copied_response: false,
    copy_zone: null,
    opened_sources: false,
    opened_details: false,
    expanded_visualization: false,
    exported_report: false,
    reran_question: false,
  });

  useEffect(() => {
    renderedAtRef.current = Date.now();
    setBehaviorSignals({
      copied_response: false,
      copy_zone: null,
      opened_sources: false,
      opened_details: false,
      expanded_visualization: false,
      exported_report: false,
      reran_question: false,
    });
  }, [response]);

  function markBehaviorSignal(partial) {
    setBehaviorSignals((current) => ({ ...current, ...partial }));
  }

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
  const canExport = hasExportableResponse(response);
  const responseMode = getResponseMode(response);
  const isExternal = responseMode === "external" || responseMode === "hybrid";
  const visibleExternalSources = isExternal ? externalSources : [];
  const getImplicitSignals = () => ({
    dwell_time_ms: Date.now() - renderedAtRef.current,
    response_char_count: getResponseCharCount(response),
    copied_response: behaviorSignals.copied_response,
    copy_zone: behaviorSignals.copy_zone,
    opened_sources: behaviorSignals.opened_sources,
    opened_details: behaviorSignals.opened_details,
    expanded_visualization: behaviorSignals.expanded_visualization,
    exported_report: behaviorSignals.exported_report,
    reran_question: behaviorSignals.reran_question,
    follow_up_question: null,
    reformulation_similarity: null,
    warnings_visible: Array.isArray(response?.warnings) && response.warnings.length > 0,
    response_had_visualization: visualizations.length > 0,
  });

  const mainText =
    insights[0] ??
    (records.length
      ? "Here's what I found based on your question."
      : "I could not find enough data to generate a full analysis.");
  const extraInsights = insights.slice(1, 4);
  const insightLabel = getResponseTask(response) === "aggregation" ? "Points clés" : "Key insights";

  const clarificationQuestion = response?.clarification_question ?? null;
  const clarificationSuggestions = Array.isArray(response?.clarification_suggestions)
    ? response.clarification_suggestions
    : [];

  return (
    <div className="message-row assistant">
      <div
        className="assistant-bubble"
        onCopy={(event) =>
          markBehaviorSignal({
            copied_response: true,
            copy_zone: detectCopyZone(event.target),
          })
        }
      >
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
          <ChartCard
            figure={visualizations[0]}
            onExpand={() => markBehaviorSignal({ expanded_visualization: true })}
          />
        )}

        {extraInsights.length > 0 && (
          <InsightsList insights={extraInsights} label={insightLabel} />
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

        <FeedbackWidget
          response={response}
          originalQuestion={originalQuestion}
          conversationId={conversationId}
          messageId={messageId}
          getImplicitSignals={getImplicitSignals}
        />

        <div className="bubble-footer">
          {canExport && (
            <ExportMenu
              response={response}
              originalQuestion={originalQuestion}
              onExport={() => markBehaviorSignal({ exported_report: true })}
            />
          )}
          {hasModelEvaluation && (
            <button
              type="button"
              className="model-evaluation-btn"
              onClick={() => {
                markBehaviorSignal({ opened_details: true });
                setShowAnalysisStats(true);
              }}
            >
              Show model evaluation
            </button>
          )}
          {hasProvenance && (
            <button
              type="button"
              className="provenance-btn"
              onClick={() => {
                markBehaviorSignal({ opened_sources: true });
                setShowProvenance(true);
              }}
            >
              Sources et méthode
            </button>
          )}
          {hasDetails && (
            <button
              type="button"
              className="details-btn"
              onClick={() => {
                markBehaviorSignal({ opened_details: true });
                setShowDetails((v) => !v);
              }}
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
