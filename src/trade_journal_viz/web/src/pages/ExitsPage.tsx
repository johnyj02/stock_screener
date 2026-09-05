import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import FilterBar from "../components/filters/FilterBar";
import SectionCard from "../components/layout/SectionCard";
import BarChartSimple from "../components/charts/BarChartSimple";
import HistogramChart from "../components/charts/HistogramChart";
import DataTable from "../components/tables/DataTable";
import { fetchExits } from "../lib/api";
import { useFilters } from "../state/useFilters";
import { formatNumber } from "../lib/format";

export default function ExitsPage() {
  const { runId = "" } = useParams();
  const { filters } = useFilters();
  const { data } = useQuery({
    queryKey: ["exits", runId, filters],
    queryFn: () => fetchExits(runId, filters),
    enabled: Boolean(runId)
  });

  const exitReasonTotals = useMemo(() => {
    const totals: Record<string, number> = {};
    data?.exit_reason?.forEach((row) => {
      totals[row.reason] = (totals[row.reason] || 0) + row.count;
    });
    return Object.entries(totals).map(([reason, count]) => ({ reason, count }));
  }, [data?.exit_reason]);

  return (
    <div className="page">
      <FilterBar />
      <div className="grid two-two">
        <SectionCard title="Exit Reason Attribution" subtitle="Counts by reason">
          <BarChartSimple data={exitReasonTotals} xKey="reason" yKey="count" color="#F97316" />
        </SectionCard>
        <SectionCard title="Giveback Distribution" subtitle="Peak to exit giveback (R)">
          <HistogramChart data={data?.giveback_distribution || []} />
        </SectionCard>
      </div>

      <SectionCard title="Worst Givebacks" subtitle="Positions with largest giveback">
        <DataTable
          columns={[
            { key: "position_id", label: "ID" },
            { key: "symbol", label: "Symbol" },
            { key: "book", label: "Book" },
            { key: "strategy", label: "Strategy" },
            { key: "entry_date", label: "Entry" },
            { key: "exit_date", label: "Exit" },
            {
              key: "giveback_r",
              label: "Giveback R",
              align: "right",
              render: (row) => formatNumber(row.giveback_r, 2)
            },
            {
              key: "realized_pnl",
              label: "PnL",
              align: "right",
              render: (row) => formatNumber(row.realized_pnl, 2)
            }
          ]}
          rows={data?.worst_givebacks || []}
        />
      </SectionCard>
    </div>
  );
}
