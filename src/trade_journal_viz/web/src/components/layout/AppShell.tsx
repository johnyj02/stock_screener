import { ReactNode } from "react";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";

interface AppShellProps {
  runId: string;
  children: ReactNode;
}

export default function AppShell({ runId, children }: AppShellProps) {
  return (
    <div className="app-shell">
      <Sidebar runId={runId} />
      <div className="app-main">
        <TopBar runId={runId} />
        <div className="app-content">{children}</div>
      </div>
    </div>
  );
}
