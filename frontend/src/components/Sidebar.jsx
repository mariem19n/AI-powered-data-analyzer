export default function Sidebar({ apiStatus, apiBase, isOpen, onClose, onNewConversation }) {
  return (
    <>
      {isOpen && (
        <div
          className="sidebar-overlay"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside className={`sidebar${isOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-icon" aria-hidden="true">AI</div>
          <div>
            <span className="brand-name">Data Analyzer</span>
            <span className="brand-tagline">Crypto market assistant</span>
          </div>
        </div>



        <div className="sidebar-spacer" />

        <button
          type="button"
          className="new-conv-btn"
          onClick={onNewConversation}
        >
          <span aria-hidden="true">+</span>
          New conversation
        </button>
      </aside>
    </>
  );
}
