import { Outlet, useParams } from "react-router-dom";
import AppShell from "./AppShell";

export default function RunLayout() {
  const params = useParams();
  const runId = params.runId || "";

  return (
    <AppShell runId={runId}>
      <Outlet />
    </AppShell>
  );
}
