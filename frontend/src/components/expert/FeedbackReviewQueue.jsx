import { useEffect, useMemo, useState } from "react";
import { fetchFeedbackQueue, reviewFeedback } from "../../services/expertApi.js";
import ExpertStatCard from "./ExpertStatCard.jsx";
import FeedbackReviewDetail from "./FeedbackReviewDetail.jsx";

export default function FeedbackReviewQueue() {
  const [data, setData] = useState({ counts: {}, items: [] });
  const [selectedId, setSelectedId] = useState(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function load() {
    setStatus("loading");
    setError("");
    try {
      const payload = await fetchFeedbackQueue();
      const nextItems = payload.items || [];
      setData({ counts: payload.counts || {}, items: nextItems });
      setSelectedId((current) => {
        if (nextItems.some((item) => item.feedback_id === current)) {
          return current;
        }
        return nextItems[0]?.feedback_id || null;
      });
      setStatus("ready");
    } catch {
      setError("Impossible de charger les feedbacks.");
      setStatus("error");
    }
  }

  useEffect(() => {
    load();
  }, []);

  const selected = useMemo(
    () => data.items.find((item) => item.feedback_id === selectedId) || null,
    [data.items, selectedId]
  );

  async function handleReview(feedbackId, payload) {
    setIsSubmitting(true);
    try {
      await reviewFeedback(feedbackId, payload);
      await load();
    } catch {
      setError("Impossible d'enregistrer la review expert.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="expert-panel feedback-review-panel">
      <div className="expert-section-header">
        <div>
          <span>Feedback Review Queue</span>
          <h2>Feedbacks en attente</h2>
        </div>
        {status === "loading" && <em>Chargement...</em>}
      </div>

      <div className="expert-stat-row">
        <ExpertStatCard label="Pending" value={data.counts.pending || 0} tone="warning" />
        <ExpertStatCard label="Accepted today" value={data.counts.accepted_today || 0} tone="healthy" />
        <ExpertStatCard label="Rejected today" value={data.counts.rejected_today || 0} tone="danger" />
      </div>

      {error && <p className="expert-error">{error}</p>}
      {status === "ready" && data.items.length === 0 && (
        <p className="expert-empty">No pending feedback.</p>
      )}

      <div className="feedback-review-layout">
        <div className="feedback-list">
          {data.items.map((item) => (
            <button
              type="button"
              key={item.feedback_id}
              className={`feedback-list-item${item.feedback_id === selectedId ? " active" : ""}`}
              onClick={() => setSelectedId(item.feedback_id)}
            >
              <span>{item.intent || "intent inconnu"}</span>
              <strong>{item.question || "Question non disponible"}</strong>
              <small>Score {item.validator_score ?? "-"} · rating {item.user_rating}/5</small>
            </button>
          ))}
        </div>
        <FeedbackReviewDetail
          feedback={selected}
          isSubmitting={isSubmitting}
          onReview={handleReview}
        />
      </div>
    </section>
  );
}
