function formatUpdatedAt(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function ConversationSidebar({
  activePage = "chat",
  apiBase,
  apiStatus,
  conversations,
  activeConversationId,
  isOpen,
  isLoading,
  onClose,
  onDeleteConversation,
  onNavigate,
  onNewConversation,
  onRenameConversation,
  onSelectConversation,
}) {
  return (
    <>
      {isOpen && (
        <div
          className="sidebar-overlay"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside className={`sidebar conversation-sidebar${isOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-icon" aria-hidden="true">AI</div>
          <div>
            <span className="brand-name">Data Analyzer</span>
            <span className="brand-tagline">Crypto market assistant</span>
          </div>
        </div>

        <div className={`api-status ${apiStatus}`}>
          <span className="status-dot" aria-hidden="true" />
          <span>
            <span className="api-status-text">
              API {apiStatus === "online" ? "online" : apiStatus}
            </span>
            <span className="api-status-url">{apiBase}</span>
          </span>
        </div>

        <nav className="main-nav" aria-label="Navigation principale">
          <button
            type="button"
            className={`main-nav-item${activePage === "dashboard" ? " active" : ""}`}
            onClick={() => onNavigate?.("dashboard")}
          >
            Dashboard
          </button>
          <button
            type="button"
            className={`main-nav-item${activePage === "chat" ? " active" : ""}`}
            onClick={() => onNavigate?.("chat")}
          >
            Chat
          </button>
        </nav>

        <button
          type="button"
          className="new-conv-btn"
          onClick={onNewConversation}
        >
          <span aria-hidden="true">+</span>
          Nouvelle conversation
        </button>

        <div className="conversation-list-header">
          <span>Conversations</span>
          {isLoading && <small>...</small>}
        </div>

        <div className="conversation-list">
          {conversations.length === 0 && !isLoading ? (
            <p className="conversation-empty">Aucune conversation sauvegardee.</p>
          ) : null}

          {conversations.map((conversation) => {
            const isActive = conversation.id === activeConversationId;
            return (
              <div
                role="button"
                tabIndex={0}
                className={`conversation-item${isActive ? " active" : ""}`}
                key={conversation.id}
                onClick={() => onSelectConversation(conversation.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectConversation(conversation.id);
                  }
                }}
              >
                <span className="conversation-main">
                  <span className="conversation-title">{conversation.title}</span>
                  <span className="conversation-time">
                    {formatUpdatedAt(conversation.updated_at)}
                  </span>
                </span>
                <span className="conversation-actions">
                  <button
                    type="button"
                    className="conversation-action"
                    title="Renommer"
                    onClick={(event) => {
                      event.stopPropagation();
                      onRenameConversation(conversation);
                    }}
                  >
                    R
                  </button>
                  <button
                    type="button"
                    className="conversation-action danger"
                    title="Supprimer"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteConversation(conversation.id);
                    }}
                  >
                    x
                  </button>
                </span>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
