export default function UserBubble({ content, isLoading = false, onRerun }) {
  return (
    <div className="message-row user">
      <div className="user-message-stack">
        <div className="user-bubble">
          <p>{content}</p>
        </div>
        <button
          type="button"
          className="rerun-question-btn"
          onClick={() => onRerun?.(content)}
          disabled={isLoading}
          title="Reposer la question"
          aria-label="Reposer la question"
        >
          <svg
            aria-hidden="true"
            className="rerun-question-icon"
            fill="none"
            height="14"
            viewBox="0 0 24 24"
            width="14"
          >
            <path
              d="M21 12a9 9 0 1 1-2.64-6.36"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
            />
            <path
              d="M21 3v6h-6"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}
