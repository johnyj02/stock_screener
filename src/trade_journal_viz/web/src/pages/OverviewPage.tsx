import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import FilterBar from "../components/filters/FilterBar";
import DrawdownChart from "../components/charts/DrawdownChart";
import EquityChart from "../components/charts/EquityChart";
import SectionCard from "../components/layout/SectionCard";
import KpiCard from "../components/layout/KpiCard";
import { fetchOverview } from "../lib/api";
import { formatCurrency, formatNumber, formatPercent } from "../lib/format";
import { useFilters } from "../state/useFilters";

export default function OverviewPage() {
  const { runId = "" } = useParams();
  const { filters } = useFilters();
  const { data, isLoading } = useQuery({
    queryKey: ["overview", runId, filters],
    queryFn: () => fetchOverview(runId, filters),
    enabled: Boolean(runId)
  });

  const kpis = data?.kpis?.portfolio || {};
  const costs =
    (data?.kpis?.core?.total_commission || 0) + (data?.kpis?.convex?.total_commission || 0) +
    (data?.kpis?.core?.total_slippage_cost || 0) + (data?.kpis?.convex?.total_slippage_cost || 0);

  return (
    <div className="page">
      <FilterBar />
      <div className="grid two-one">
        <SectionCard title="Equity Curve" subtitle="Portfolio + books">
          {isLoading ? <div className="muted">Loading equity...</div> : null}
          <EquityChart data={data?.equity || []} />
        </SectionCard>
        <div className="kpi-stack">
          <KpiCard label="CAGR" value={formatPercent(kpis.cagr_pct)} />
          <KpiCard label="Max Drawdown" value={formatPercent(kpis.max_drawdown_pct)} />
          <KpiCard label="Sharpe" value={formatNumber(kpis.sharpe_ratio)} />
          <KpiCard label="Volatility" value={formatPercent(kpis.annualized_volatility_pct)} />
          <KpiCard label="Total Return" value={formatPercent(kpis.total_return_pct)} />
          <KpiCard label="Costs" value={formatCurrency(costs)} hint="Commission + slippage" />
        </div>
      </div>

      <div className="grid two-two">
        <SectionCard title="Drawdown" subtitle="Depth and persistence">
          <DrawdownChart data={data?.drawdown || []} />
        </SectionCard>
        <SectionCard title="Monthly Returns" subtitle="Seasonality snapshot">
          <div className="monthly-grid">
            {(data?.monthly_returns || []).map((row) => (
              <div key={row.month} className="monthly-cell">
                <div className="monthly-month">{row.month}</div>
                <div className={`monthly-value ${row.return_pct >= 0 ? "pos" : "neg"}`}>
                  {formatPercent(row.return_pct, 2)}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    </div>
  );
}
