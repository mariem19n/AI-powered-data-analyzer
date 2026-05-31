import Sidebar from "./Sidebar.jsx";
import TopBar from "../TopBar.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export default function AppShell({
  activePage,
  apiStatus,
  children,
  onNavigate,
  sidebarOpen,
  title,
  onCloseSidebar,
  onToggleSidebar,
}) {
  return (
    <div className="app-shell">
      <Sidebar
        activePage={activePage}
        apiBase={API_BASE_URL}
        apiStatus={apiStatus}
        isOpen={sidebarOpen}
        onClose={onCloseSidebar}
        onNavigate={onNavigate}
      />
      <div className="main-area">
        <TopBar title={title} onToggleSidebar={onToggleSidebar} />
        {children}
      </div>
    </div>
  );
}
