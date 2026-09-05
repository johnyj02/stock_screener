import { Navigate, Route, Routes } from "react-router-dom";
import RunLayout from "./components/layout/RunLayout";
import HomePage from "./pages/HomePage";
import OverviewPage from "./pages/OverviewPage";
import RiskPage from "./pages/RiskPage";
import AllocatorPage from "./pages/AllocatorPage";
import AttributionPage from "./pages/AttributionPage";
import ExitsPage from "./pages/ExitsPage";
import JournalPage from "./pages/JournalPage";
import ComparePage from "./pages/ComparePage";
import ChartsPage from "./pages/ChartsPage";
import DailyActivityPage from "./pages/DailyActivityPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/compare" element={<ComparePage />} />
      <Route path="/runs/:runId" element={<RunLayout />}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<OverviewPage />} />
        <Route path="risk" element={<RiskPage />} />
        <Route path="allocator" element={<AllocatorPage />} />
        <Route path="attribution" element={<AttributionPage />} />
        <Route path="exits" element={<ExitsPage />} />
        <Route path="journal" element={<JournalPage />} />
        <Route path="charts" element={<ChartsPage />} />
        <Route path="daily-activity" element={<DailyActivityPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
