import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import FilterBar from "../components/filters/FilterBar";
import SectionCard from "../components/layout/SectionCard";
import MultiLineChart from "../components/charts/MultiLineChart";
import DataTable from "../components/tables/DataTable";
import { fetchRisk } from "../lib/api";
import { useFilters } from "../state/useFilters";
import { formatNumber } from "../lib/format";
import { bookColors } from "../lib/colors";

export default function RiskPage() {
  const { runId = "" } = useParams();
  const { filters } = useFilters();
  const { data } = useQuery({
    queryKey: ["risk", runId, filters],
    queryFn: () => fetchRisk(runId, filters),
    enabled: Boolean(runId)
  });

  const series = [
    { key: "core", label: "Core", color: bookColors.core },
    { key: "convex", label: "Convex", color: bookColors.convex }
  ];

  return (
    <div className="page">
      <FilterBar />
      <div className="grid two-two">
        <SectionCard title="Gross Exposure" subtitle="By book">
          <MultiLineChart data={data?.exposure || []} series={series} />
        </SectionCard>
        <SectionCard title="Open Risk" subtitle="Risk dollars by book">
          <MultiLineChart data={data?.open_risk || []} series={series} />
        </SectionCard>
      </div>

      <div className="grid two-two">
        <SectionCard title="Open Positions" subtitle="Concurrent positions by book">
          <MultiLineChart data={data?.open_positions || []} series={series} />
        </SectionCard>
        <SectionCard title="Top Contributors" subtitle="PnL concentration by symbol">
          <DataTable
            columns={[
              { key: "symbol", label: "Symbol" },
              {
                key: "total_pnl",
                label: "Total PnL",
                align: "right",
                render: (row) => formatNumber(row.total_pnl, 2)
              },
              {
                key: "trades",
                label: "Trades",
                align: "right",
                render: (row) => formatNumber(row.trades, 0)
              }
            ]}
            rows={data?.top_contributors || []}
          />
        </SectionCard>
      </div>
    </div>
  );
}
