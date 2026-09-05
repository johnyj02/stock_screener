import { useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { fetchOverview, fetchRuns } from "../lib/api";
import SectionCard from "../components/layout/SectionCard";
import DataTable from "../components/tables/DataTable";
import MultiLineChart from "../components/charts/MultiLineChart";
import { formatNumber, formatPercent } from "../lib/format";
import type { OverviewResponse } from "../lib/types";

const palette = ["#7AA2FF", "#F6C453", "#2DD4BF", "#F06565", "#C084FC"];

export default function ComparePage() {
  const { data: runs } = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const [selected, setSelected] = useState<string[]>([]);
  const [baseline, setBaseline] = useState<string>("");

  const overviewQueries = useQueries({
    queries: selected.map((runId) => ({
      queryKey: ["compare-overview", runId],
      queryFn: () => fetchOverview(runId, {}),
      enabled: Boolean(runId)
    }))
  });

  const overviewByRun = useMemo(() => {
    const map: Record<string, OverviewResponse> = {};
    overviewQueries.forEach((query, idx) => {
      if (query.data) {
        map[selected[idx]] = query.data;
      }
    });
    return map;
  }, [overviewQueries, selected]);

  const handleToggle = (runId: string) => {
    setSelected((prev) => {
      if (prev.includes(runId)) {
        const next = prev.filter((id) => id !== runId);
        if (baseline === runId) {
          setBaseline(next[0] || "");
        }
        return next;
      }
      if (prev.length >= 5) {
        return prev;
      }
      const next = [...prev, runId];
      if (!baseline) {
        setBaseline(runId);
      }
      return next;
    });
  };

  const scoreboard = useMemo(() => {
    return selected.map((runId) => {
      const kpis = overviewByRun[runId]?.kpis?.portfolio || {};
      const baselineKpis = baseline ? overviewByRun[baseline]?.kpis?.portfolio || {} : {};
      return {
        runId,
        cagr_pct: kpis.cagr_pct || 0,
        max_drawdown_pct: kpis.max_drawdown_pct || 0,
        sharpe_ratio: kpis.sharpe_ratio || 0,
        delta_cagr: baseline ? (kpis.cagr_pct || 0) - (baselineKpis.cagr_pct || 0) : 0,
        delta_dd: baseline ? (kpis.max_drawdown_pct || 0) - (baselineKpis.max_drawdown_pct || 0) : 0,
        delta_sharpe: baseline ? (kpis.sharpe_ratio || 0) - (baselineKpis.sharpe_ratio || 0) : 0
      };
    });
  }, [selected, overviewByRun, baseline]);

  const chartData = useMemo(() => {
    const map: Record<string, Record<string, number | string>> = {};
    selected.forEach((runId) => {
      const equity = overviewByRun[runId]?.equity || [];
      const firstValue = equity[0]?.total || 1;
      equity.forEach((point) => {
        if (!map[point.date]) {
          map[point.date] = { date: point.date };
        }
        map[point.date][runId] = firstValue ? point.total / firstValue : point.total;
      });
    });
    return Object.values(map).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [overviewByRun, selected]);

  const series = selected.map((runId, idx) => ({
    key: runId,
    label: runId,
    color: palette[idx % palette.length]
  }));

  return (
    <div className="page">
      <div className="compare-layout">
        <SectionCard title="Select Runs" subtitle="Up to 5 runs for comparison">
          <div className="compare-list">
            {runs?.map((run) => (
              <label key={run.run_id} className="compare-item">
                <input
                  type="checkbox"
                  checked={selected.includes(run.run_id)}
                  onChange={() => handleToggle(run.run_id)}
                />
                <span>{run.run_id}</span>
              </label>
            ))}
          </div>
          <div className="compare-baseline">
            <span className="muted">Baseline</span>
            <select value={baseline} onChange={(event) => setBaseline(event.target.value)}>
              <option value="">None</option>
              {selected.map((runId) => (
                <option key={runId} value={runId}>
                  {runId}
                </option>
              ))}
            </select>
          </div>
        </SectionCard>

        <SectionCard title="Scoreboard" subtitle="Deltas vs baseline">
          <DataTable
            columns={[
              { key: "runId", label: "Run" },
              {
                key: "cagr_pct",
                label: "CAGR",
                align: "right",
                render: (row) => formatPercent(row.cagr_pct, 2)
              },
              {
                key: "delta_cagr",
                label: "Delta",
                align: "right",
                render: (row) => formatPercent(row.delta_cagr, 2)
              },
              {
                key: "max_drawdown_pct",
                label: "Max DD",
                align: "right",
                render: (row) => formatPercent(row.max_drawdown_pct, 2)
              },
              {
                key: "delta_dd",
                label: "Delta",
                align: "right",
                render: (row) => formatPercent(row.delta_dd, 2)
              },
              {
                key: "sharpe_ratio",
                label: "Sharpe",
                align: "right",
                render: (row) => formatNumber(row.sharpe_ratio, 2)
              },
              {
                key: "delta_sharpe",
                label: "Delta",
                align: "right",
                render: (row) => formatNumber(row.delta_sharpe, 2)
              }
            ]}
            rows={scoreboard}
          />
        </SectionCard>
      </div>

      <SectionCard title="Equity Overlay" subtitle="Normalized to 1.0 at start">
        <MultiLineChart data={chartData} series={series} />
      </SectionCard>
    </div>
  );
}
