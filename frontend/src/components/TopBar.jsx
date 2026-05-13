export default function TopBar({ onToggleSidebar }) {
  return (
    <header className="topbar">
      <button
        type="button"
        className="hamburger"
        onClick={onToggleSidebar}
        aria-label="Toggle sidebar"
      >
        ☰
      </button>
      <span className="topbar-title">Crypto Market Assistant</span>
    </header>
  );
}
