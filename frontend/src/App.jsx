import AppErrorBoundary from "./components/AppErrorBoundary.jsx";
import DashboardPage from "./components/DashboardPage.jsx";
import "./App.css";

export default function App() {
  return (
    <AppErrorBoundary>
      <DashboardPage />
    </AppErrorBoundary>
  );
}
