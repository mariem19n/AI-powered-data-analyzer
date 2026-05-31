import { useEffect, useState } from "react";
import AppShell from "../components/layout/AppShell.jsx";
import MarketOverview from "../components/dashboard/MarketOverview.jsx";
import MacroPulse from "../components/dashboard/MacroPulse.jsx";
import SentimentRadar from "../components/dashboard/SentimentRadar.jsx";
import RecentAnomalies from "../components/dashboard/RecentAnomalies.jsx";
import CorrelationMini from "../components/dashboard/CorrelationMini.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export default function DashboardPage({ activePage, onNavigate, onOpenChatQuestion }) {
  const [apiStatus, setApiStatus] = useState("checking");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/health`, { signal: controller.signal })
      .then((r) => setApiStatus(r.ok ? "online" : "degraded"))
      .catch(() => setApiStatus("offline"));
    return () => controller.abort();
  }, []);

  return (
    <AppShell
      activePage={activePage}
      apiStatus={apiStatus}
      title="Dashboard"
      sidebarOpen={sidebarOpen}
      onCloseSidebar={() => setSidebarOpen(false)}
      onNavigate={(page) => {
        setSidebarOpen(false);
        onNavigate?.(page);
      }}
      onToggleSidebar={() => setSidebarOpen((value) => !value)}
    >
      <main className="dashboard-scroll">
        <MarketOverview onOpenChatQuestion={onOpenChatQuestion} />
        <div className="dashboard-two-col">
          <MacroPulse />
          <SentimentRadar />
        </div>
        <div className="dashboard-two-col lower">
          <RecentAnomalies />
          <CorrelationMini />
        </div>
      </main>
    </AppShell>
  );
}
