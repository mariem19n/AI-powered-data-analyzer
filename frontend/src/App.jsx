import AppErrorBoundary from "./components/AppErrorBoundary.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import ChatPage from "./pages/ChatPage.jsx";
import ExpertDashboardPage from "./pages/ExpertDashboardPage.jsx";
import "./App.css";
import { useEffect, useState } from "react";

function getInitialPage() {
  if (window.location.pathname === "/expert") return "expert";
  if (window.location.pathname === "/chat") return "chat";
  if (window.location.pathname === "/dashboard" || window.location.pathname === "/") return "dashboard";
  return window.location.hash === "#chat" ? "chat" : "dashboard";
}

export default function App() {
  const [activePage, setActivePage] = useState(getInitialPage);
  const [prefillQuestion, setPrefillQuestion] = useState("");

  useEffect(() => {
    function handleNavigationChange() {
      setActivePage(getInitialPage());
    }
    window.addEventListener("hashchange", handleNavigationChange);
    window.addEventListener("popstate", handleNavigationChange);
    return () => {
      window.removeEventListener("hashchange", handleNavigationChange);
      window.removeEventListener("popstate", handleNavigationChange);
    };
  }, []);

  function navigate(page) {
    setActivePage(page);
    if (page === "expert") {
      window.history.pushState(null, "", "/expert");
      return;
    }
    window.history.pushState(null, "", page === "chat" ? "/chat" : "/dashboard");
  }

  function openChatWithQuestion(question) {
    setPrefillQuestion(question);
    navigate("chat");
  }

  return (
    <AppErrorBoundary>
      {activePage === "expert" ? (
        <ExpertDashboardPage />
      ) : activePage === "chat" ? (
        <ChatPage
          activePage={activePage}
          onNavigate={navigate}
          prefillQuestion={prefillQuestion}
        />
      ) : (
        <DashboardPage
          activePage={activePage}
          onNavigate={navigate}
          onOpenChatQuestion={openChatWithQuestion}
        />
      )}
    </AppErrorBoundary>
  );
}
