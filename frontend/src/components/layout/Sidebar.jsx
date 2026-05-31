export default function Sidebar({
  activePage,
  apiBase,
  apiStatus = "checking",
  isOpen = false,
  onClose,
  onNavigate,
}) {
  return (
    <>
      {isOpen && (
        <div className="sidebar-overlay" onClick={onClose} aria-hidden="true" />
      )}
      <aside className={`sidebar${isOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-icon" aria-hidden="true">AI</div>
          <div>
            <span className="brand-name">Data Analyzer</span>
            <span className="brand-tagline">Market intelligence</span>
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

        <div className="sidebar-spacer" />
      </aside>
    </>
  );
}
