import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import FilterBar from "../components/filters/FilterBar";
import SectionCard from "../components/layout/SectionCard";
import BarChartSimple from "../components/charts/BarChartSimple";
import DataTable from "../components/tables/DataTable";
import { fetchAttribution } from "../lib/api";
import { useFilters } from "../state/useFilters";
import { formatNumber, formatPercent } from "../lib/format";

export default function AttributionPage() {
  const { runId = "" } = useParams();
  const { filters } = useFilters();
  const { data, isLoading } = useQuery({
    queryKey: ["attribution", runId, filters],
    queryFn: () => fetchAttribution(runId, filters),
    enabled: Boolean(runId)
  });

  const pnlByStrategy = useMemo(() => {
    const totals: Record<string, number> = {};
    data?.pnl_by_strategy?.forEach((row) => {
      totals[row.strategy] = (totals[row.strategy] || 0) + row.total_pnl;
    });
    return Object.entries(totals).map(([strategy, total_pnl]) => ({ strategy, total_pnl }));
  }, [data?.pnl_by_strategy]);

  return (
    <div className="page">
      <FilterBar />
      <div className="grid two-two">
        <SectionCard title="Strategy Leaderboard" subtitle="Expectancy and stability">
          {isLoading ? <div className="muted">Loading strategies...</div> : null}
          <DataTable
            columns={[
              { key: "book", label: "Book" },
              { key: "strategy", label: "Strategy" },
              { key: "trades", label: "Trades", align: "right", render: (row) => formatNumber(row.trades, 0) },
              {
                key: "win_rate_pct",
                label: "Win Rate",
                align: "right",
                render: (row) => formatPercent(row.win_rate_pct, 2)
              },
              {
                key: "expectancy",
                label: "Expectancy",
                align: "right",
                render: (row) => formatNumber(row.expectancy, 2)
              },
              {
                key: "total_pnl",
                label: "Total PnL",
                align: "right",
                render: (row) => formatNumber(row.total_pnl, 2)
              }
            ]}
            rows={data?.strategy_leaderboard || []}
          />
        </SectionCard>
        <SectionCard title="PnL by Strategy" subtitle="Total contribution">
          <BarChartSimple data={pnlByStrategy} xKey="strategy" yKey="total_pnl" color="#7AA2FF" />
        </SectionCard>
      </div>

      <SectionCard title="Exit Reason Attribution" subtitle="How exits shape outcomes">
        <DataTable
          columns={[
            { key: "book", label: "Book" },
            { key: "strategy", label: "Strategy" },
            { key: "reason", label: "Reason" },
            { key: "stop_type_at_exit", label: "Stop" },
            { key: "count", label: "Count", align: "right", render: (row) => formatNumber(row.count, 0) },
            {
              key: "total_pnl",
              label: "Total PnL",
              align: "right",
              render: (row) => formatNumber(row.total_pnl, 2)
            }
          ]}
          rows={data?.exit_reason_attribution || []}
        />
      </SectionCard>
    </div>
  );
}
