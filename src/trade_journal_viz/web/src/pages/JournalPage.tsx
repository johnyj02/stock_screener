import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import FilterBar from "../components/filters/FilterBar";
import SectionCard from "../components/layout/SectionCard";
import { fetchPositionDetail, fetchPositions } from "../lib/api";
import { useFilters } from "../state/useFilters";
import { formatNumber, formatPercent } from "../lib/format";

export default function JournalPage() {
  const { runId = "" } = useParams();
  const { filters } = useFilters();
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const positionsQuery = useQuery({
    queryKey: ["positions", runId, filters],
    queryFn: () => fetchPositions(runId, filters),
    enabled: Boolean(runId)
  });

  const detailQuery = useQuery({
    queryKey: ["position-detail", runId, selectedId],
    queryFn: () => fetchPositionDetail(runId, selectedId as number),
    enabled: Boolean(runId && selectedId)
  });

  return (
    <div className="page">
      <FilterBar />
      <div className="grid journal-grid">
        <SectionCard title="Positions" subtitle="Click a row to open details">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th className="align-right">Exit R</th>
                  <th className="align-right">Giveback R</th>
                  <th className="align-right">PnL</th>
                </tr>
              </thead>
              <tbody>
                {positionsQuery.data?.rows?.map((row) => (
                  <tr
                    key={row.position_id}
                    className={selectedId === row.position_id ? "active" : ""}
                    onClick={() => setSelectedId(row.position_id)}
                  >
                    <td>{row.position_id}</td>
                    <td>{row.symbol}</td>
                    <td>{row.strategy}</td>
                    <td>{row.entry_date}</td>
                    <td>{row.exit_date}</td>
                    <td className="align-right">{formatNumber(row.exit_r, 2)}</td>
                    <td className="align-right">{formatNumber(row.giveback_r, 2)}</td>
                    <td className="align-right">{formatNumber(row.realized_pnl, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>

        <SectionCard title="Trade Detail" subtitle="Allocator state, efficiency, and costs">
          {!selectedId ? <div className="muted">Select a position to inspect.</div> : null}
          {detailQuery.data?.position ? (
            <div className="trade-detail">
              <div className="detail-block">
                <div className="detail-title">Summary</div>
                <div className="detail-grid">
                  <div>
                    <div className="detail-label">Symbol</div>
                    <div className="detail-value">{detailQuery.data.position.symbol}</div>
                  </div>
                  <div>
                    <div className="detail-label">Strategy</div>
                    <div className="detail-value">{detailQuery.data.position.strategy}</div>
                  </div>
                  <div>
                    <div className="detail-label">Entry</div>
                    <div className="detail-value">{detailQuery.data.position.entry_date}</div>
                  </div>
                  <div>
                    <div className="detail-label">Exit</div>
                    <div className="detail-value">{detailQuery.data.position.exit_date}</div>
                  </div>
                </div>
              </div>

              <div className="detail-block">
                <div className="detail-title">Quality</div>
                <div className="detail-grid">
                  <div>
                    <div className="detail-label">Peak R</div>
                    <div className="detail-value">{formatNumber(detailQuery.data.position.peak_r, 2)}</div>
                  </div>
                  <div>
                    <div className="detail-label">Exit R</div>
                    <div className="detail-value">{formatNumber(detailQuery.data.position.exit_r, 2)}</div>
                  </div>
                  <div>
                    <div className="detail-label">Giveback R</div>
                    <div className="detail-value">{formatNumber(detailQuery.data.position.giveback_r, 2)}</div>
                  </div>
                  <div>
                    <div className="detail-label">Return</div>
                    <div className="detail-value">{formatPercent(detailQuery.data.position.return_pct, 2)}</div>
                  </div>
                </div>
              </div>

              <div className="detail-block">
                <div className="detail-title">Costs</div>
                <div className="detail-grid">
                  <div>
                    <div className="detail-label">Commission</div>
                    <div className="detail-value">{formatNumber(detailQuery.data.position.costs_commission, 2)}</div>
                  </div>
                  <div>
                    <div className="detail-label">Slippage</div>
                    <div className="detail-value">{formatNumber(detailQuery.data.position.costs_slippage, 2)}</div>
                  </div>
                  <div>
                    <div className="detail-label">Allocator Entry</div>
                    <div className="detail-value">{detailQuery.data.position.allocator_state_at_entry}</div>
                  </div>
                  <div>
                    <div className="detail-label">Allocator Exit</div>
                    <div className="detail-value">{detailQuery.data.position.allocator_state_at_exit}</div>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </SectionCard>
      </div>
    </div>
  );
}
