export default function TopBar({ onToggleSidebar, title = "Crypto Market Assistant" }) {
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
      <span className="topbar-title">{title}</span>
    </header>
  );
}
