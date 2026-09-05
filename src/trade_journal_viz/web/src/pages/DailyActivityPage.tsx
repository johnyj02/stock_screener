import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import SectionCard from "../components/layout/SectionCard";
import DataTable, { type Column } from "../components/tables/DataTable";
import { fetchDailyActivity } from "../lib/api";
import { formatNumber } from "../lib/format";
import type { PositionSummary } from "../lib/types";

export default function DailyActivityPage() {
  const { runId = "" } = useParams();
  const { data, isLoading } = useQuery({
    queryKey: ["daily-activity", runId],
    queryFn: () => fetchDailyActivity(runId),
    enabled: Boolean(runId)
  });

  const asOf = data?.as_of_date || "";

  const columns: Column<PositionSummary>[] = [
    { key: "position_id", label: "ID" },
    { key: "symbol", label: "Symbol" },
    { key: "book", label: "Book" },
    { key: "strategy", label: "Strategy" },
    { key: "entry_date", label: "Entry" },
    { key: "exit_date", label: "Exit" },
    {
      key: "exit_r",
      label: "Exit R",
      align: "right",
      render: (row) => formatNumber(row.exit_r, 2)
    },
    {
      key: "realized_pnl",
      label: "PnL",
      align: "right",
      render: (row) => formatNumber(row.realized_pnl, 2)
    },
    {
      key: "giveback_r",
      label: "Giveback R",
      align: "right",
      render: (row) => formatNumber(row.giveback_r, 2)
    }
  ];

  return (
    <div className="page">
      <SectionCard title="Daily Activity" subtitle={`As of ${asOf || "-"}`}>
        {isLoading && <div className="muted">Loading daily activity...</div>}
        {!isLoading && !asOf ? <div className="muted">No dates found for this run.</div> : null}
      </SectionCard>

      <SectionCard title="Opened on Current Date" subtitle={asOf ? `Opened on ${asOf}` : ""}>
        <DataTable columns={columns} rows={data?.opened || []} />
      </SectionCard>

      <SectionCard title="Closed on Last Backtest Date" subtitle={asOf ? `Closed on ${asOf}` : ""}>
        <DataTable columns={columns} rows={data?.closed || []} />
      </SectionCard>
    </div>
  );
}
