import { useState } from "react";

function JsonBlock({ value }) {
  if (!value || (typeof value === "object" && Object.keys(value).length === 0)) {
    return <p className="expert-empty-inline">Aucune donnée.</p>;
  }
  return <pre className="expert-json-block">{JSON.stringify(value, null, 2)}</pre>;
}

export default function FeedbackReviewDetail({ feedback, onReview, isSubmitting }) {
  const [expertScore, setExpertScore] = useState("0.8");
  const [expertNote, setExpertNote] = useState("");

  if (!feedback) {
    return (
      <section className="expert-detail empty">
        <p>Sélectionnez un feedback pour afficher son détail.</p>
      </section>
    );
  }

  function submit(decision) {
    onReview?.(feedback.feedback_id, {
      decision,
      expert_score: Number(expertScore),
      expert_note: expertNote.trim() || null,
    });
  }

  return (
    <section className="expert-detail">
      <div className="expert-detail-header">
        <div>
          <span className={`expert-status ${feedback.validation_status}`}>
            {feedback.validation_status}
          </span>
          <h3>{feedback.question || "Question non disponible"}</h3>
        </div>
        <strong>Rating {feedback.user_rating ?? "-"}/5</strong>
      </div>

      <div className="expert-detail-grid">
        <div>
          <span>Réponse</span>
          <p>{feedback.response_summary || "Aperçu non disponible."}</p>
        </div>
        <div>
          <span>Commentaire utilisateur</span>
          <p>{feedback.user_comment || "Aucun commentaire."}</p>
        </div>
        <div>
          <span>Verdict validator</span>
          <p>{feedback.validator_verdict || "Non disponible."}</p>
        </div>
        <div>
          <span>Score calculé</span>
          <p>{feedback.validator_score ?? "Non disponible"}</p>
        </div>
      </div>

      <details className="expert-details">
        <summary>Signaux implicites</summary>
        <JsonBlock value={feedback.implicit_signals} />
      </details>

      <div className="expert-review-form">
        <label>
          Expert score
          <input
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={expertScore}
            onChange={(event) => setExpertScore(event.target.value)}
          />
        </label>
        <label>
          Note expert
          <textarea
            rows={2}
            value={expertNote}
            onChange={(event) => setExpertNote(event.target.value)}
            placeholder="Ajouter une note de validation..."
          />
        </label>
      </div>

      <div className="expert-actions">
        <button type="button" className="accept" disabled={isSubmitting} onClick={() => submit("accepted")}>
          Confirmer
        </button>
        <button type="button" className="reject" disabled={isSubmitting} onClick={() => submit("rejected")}>
          Rejeter
        </button>
        <button type="button" className="pending" disabled={isSubmitting} onClick={() => submit("pending_review")}>
          À revoir
        </button>
      </div>
    </section>
  );
}
