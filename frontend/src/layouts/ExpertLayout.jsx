import { useEffect, useState } from "react";
import TopBar from "../components/TopBar.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const EXPERT_SECTIONS = [
  { id: "expert-overview", label: "Overview" },
  { id: "expert-feedback-queue", label: "Feedback Queue" },
  { id: "expert-response-quality", label: "Response Quality" },
  { id: "expert-knowledge-graph", label: "Knowledge Graph" },
  { id: "expert-prompt-performance", label: "Prompt Performance" },
  { id: "expert-data-freshness", label: "Data Freshness" },
  { id: "expert-semantic-health", label: "Semantic Health" },
];

export default function ExpertLayout({ children }) {
  const [apiStatus, setApiStatus] = useState("checking");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeSection, setActiveSection] = useState(EXPERT_SECTIONS[0].id);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/health`, { signal: controller.signal })
      .then((response) => setApiStatus(response.ok ? "online" : "degraded"))
      .catch(() => setApiStatus("offline"));
    return () => controller.abort();
  }, []);

  function scrollToSection(sectionId) {
    setActiveSection(sectionId);
    setSidebarOpen(false);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="app-shell expert-shell">
      {sidebarOpen && <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} aria-hidden="true" />}
      <aside className={`sidebar expert-sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-icon" aria-hidden="true">EX</div>
          <div>
            <span className="brand-name">Expert Console</span>
            <span className="brand-tagline">System quality supervision</span>
          </div>
        </div>

        <div className={`api-status ${apiStatus}`}>
          <span className="status-dot" aria-hidden="true" />
          <span>
            <span className="api-status-text">API {apiStatus === "online" ? "online" : apiStatus}</span>
            <span className="api-status-url">{API_BASE_URL}</span>
          </span>
        </div>

        <nav className="main-nav" aria-label="Navigation expert">
          {EXPERT_SECTIONS.map((section) => (
            <button
              key={section.id}
              type="button"
              className={`main-nav-item${activeSection === section.id ? " active" : ""}`}
              onClick={() => scrollToSection(section.id)}
            >
              {section.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-spacer" />
      </aside>

      <div className="main-area">
        <TopBar title="Expert Dashboard" onToggleSidebar={() => setSidebarOpen((value) => !value)} />
        {children}
      </div>
    </div>
  );
}
