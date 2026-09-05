import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import FilterBar from "../components/filters/FilterBar";
import SectionCard from "../components/layout/SectionCard";
import BarChartSimple from "../components/charts/BarChartSimple";
import LineChartSimple from "../components/charts/LineChartSimple";
import DataTable from "../components/tables/DataTable";
import { fetchAllocator } from "../lib/api";
import { useFilters } from "../state/useFilters";
import { formatPercent } from "../lib/format";

export default function AllocatorPage() {
  const { runId = "" } = useParams();
  const { filters } = useFilters();
  const { data, isLoading } = useQuery({
    queryKey: ["allocator", runId, filters],
    queryFn: () => fetchAllocator(runId, filters),
    enabled: Boolean(runId)
  });

  const rejectionSummary = useMemo(() => {
    const summary: Record<string, number> = {};
    data?.rejections?.forEach((item) => {
      summary[item.reason] = (summary[item.reason] || 0) + item.count;
    });
    return Object.entries(summary).map(([reason, count]) => ({ reason, count }));
  }, [data?.rejections]);

  const acceptanceSeries = useMemo(() => {
    const byDate: Record<string, { total: number; count: number }> = {};
    data?.acceptance_rate?.forEach((row) => {
      if (!byDate[row.date]) {
        byDate[row.date] = { total: 0, count: 0 };
      }
      byDate[row.date].total += row.acceptance_rate;
      byDate[row.date].count += 1;
    });
    return Object.entries(byDate).map(([date, value]) => ({
      date,
      acceptance_rate: value.count ? value.total / value.count : 0
    }));
  }, [data?.acceptance_rate]);

  const latestStates = useMemo(() => {
    const latest: Record<string, string> = {};
    data?.state_timeline?.forEach((row) => {
      latest[row.book] = row.state;
    });
    return latest;
  }, [data?.state_timeline]);

  return (
    <div className="page">
      <FilterBar />

      <div className="grid two-two">
        <SectionCard title="Allocator State" subtitle="Current status by book">
          {isLoading ? <div className="muted">Loading states...</div> : null}
          <div className="state-cards">
            <div className="state-card">
              <div className="state-label">Core</div>
              <div className="state-value">{latestStates.core || "-"}</div>
            </div>
            <div className="state-card">
              <div className="state-label">Convex</div>
              <div className="state-value">{latestStates.convex || "-"}</div>
            </div>
          </div>
        </SectionCard>
        <SectionCard title="Rejection Reasons" subtitle="Aggregated across period">
          <BarChartSimple data={rejectionSummary} xKey="reason" yKey="count" color="#F6C453" />
        </SectionCard>
      </div>

      <div className="grid two-two">
        <SectionCard title="Acceptance Rate" subtitle="Daily acceptance across books">
          <LineChartSimple data={acceptanceSeries} xKey="date" yKey="acceptance_rate" color="#2DD4BF" />
        </SectionCard>
        <SectionCard title="State Change Events" subtitle="Triggers and risk budget changes">
          <DataTable
            columns={[
              { key: "date", label: "Date" },
              { key: "book", label: "Book" },
              { key: "prev_state", label: "Prev" },
              { key: "new_state", label: "New" },
              { key: "trigger", label: "Trigger" },
              {
                key: "risk_budget_after",
                label: "Budget After",
                align: "right",
                render: (row) => formatPercent(row.risk_budget_after, 2)
              }
            ]}
            rows={data?.events || []}
          />
        </SectionCard>
      </div>
    </div>
  );
}
