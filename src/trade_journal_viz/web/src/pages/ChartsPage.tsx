import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import SectionCard from "../components/layout/SectionCard";
import { fetchCharts, chartUrl } from "../lib/api";

export default function ChartsPage() {
  const { runId = "" } = useParams();
  const [search, setSearch] = useState("");
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [filters, setFilters] = useState({
    symbol: "all",
    strategy: "all",
    book: "all",
    outcome: "all",
    exit_reason: "all",
    stop_type: "all"
  });

  const { data, isLoading } = useQuery({
    queryKey: ["charts", runId],
    queryFn: () => fetchCharts(runId),
    enabled: Boolean(runId)
  });

  const options = useMemo(() => {
    if (!data) {
      return {
        symbols: [],
        strategies: [],
        books: [],
        outcomes: [],
        exitReasons: [],
        stopTypes: []
      };
    }
    const symbols = new Set<string>();
    const strategies = new Set<string>();
    const books = new Set<string>();
    const outcomes = new Set<string>();
    const exitReasons = new Set<string>();
    const stopTypes = new Set<string>();
    data.forEach((item) => {
      if (item.symbol) symbols.add(item.symbol);
      if (item.strategy) strategies.add(item.strategy);
      if (item.book) books.add(item.book);
      if (item.outcome) outcomes.add(item.outcome);
      if (item.exit_reason) exitReasons.add(item.exit_reason);
      if (item.stop_type_at_exit) stopTypes.add(item.stop_type_at_exit);
    });
    return {
      symbols: Array.from(symbols).sort(),
      strategies: Array.from(strategies).sort(),
      books: Array.from(books).sort(),
      outcomes: Array.from(outcomes).sort(),
      exitReasons: Array.from(exitReasons).sort(),
      stopTypes: Array.from(stopTypes).sort()
    };
  }, [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const term = search.trim().toLowerCase();
    return data.filter((item) => {
      if (term) {
        const haystack = [item.filename, item.symbol, item.strategy]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(term)) {
          return false;
        }
      }
      if (filters.symbol !== "all" && item.symbol !== filters.symbol) return false;
      if (filters.strategy !== "all" && item.strategy !== filters.strategy) return false;
      if (filters.book !== "all" && item.book !== filters.book) return false;
      if (filters.outcome !== "all" && item.outcome !== filters.outcome) return false;
      if (filters.exit_reason !== "all" && item.exit_reason !== filters.exit_reason) return false;
      if (filters.stop_type !== "all" && item.stop_type_at_exit !== filters.stop_type) return false;
      return true;
    });
  }, [data, search, filters]);

  const selected = useMemo(() => {
    if (selectedIdx === null) return null;
    return filtered[selectedIdx] ?? null;
  }, [filtered, selectedIdx]);

  const clampZoom = (value: number) => Math.min(Math.max(value, 0.2), 10);

  const zoomIn = () => setZoom((z) => clampZoom(z + 0.25));
  const zoomOut = () => setZoom((z) => clampZoom(z - 0.25));
  const resetZoom = () => setZoom(1);

  return (
    <div className="page">
      <SectionCard title="Trade Charts" subtitle="Entry and exit snapshots per symbol">
        <div className="chart-toolbar">
          <input
            className="chart-search"
            placeholder="Filter by symbol (e.g., AAPL)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="muted small">{filtered.length} charts</div>
        </div>

        <div className="filter-bar">
          <div className="filter-row">
            <label>
              Symbol
              <select
                value={filters.symbol}
                onChange={(event) => setFilters({ ...filters, symbol: event.target.value })}
              >
                <option value="all">All</option>
                {options.symbols.map((symbol) => (
                  <option key={symbol} value={symbol}>{symbol}</option>
                ))}
              </select>
            </label>
            <label>
              Strategy
              <select
                value={filters.strategy}
                onChange={(event) => setFilters({ ...filters, strategy: event.target.value })}
              >
                <option value="all">All</option>
                {options.strategies.map((strategy) => (
                  <option key={strategy} value={strategy}>{strategy}</option>
                ))}
              </select>
            </label>
            <label>
              Book
              <select
                value={filters.book}
                onChange={(event) => setFilters({ ...filters, book: event.target.value })}
              >
                <option value="all">All</option>
                {options.books.map((book) => (
                  <option key={book} value={book}>{book}</option>
                ))}
              </select>
            </label>
            <label>
              Outcome
              <select
                value={filters.outcome}
                onChange={(event) => setFilters({ ...filters, outcome: event.target.value })}
              >
                <option value="all">All</option>
                {options.outcomes.map((outcome) => (
                  <option key={outcome} value={outcome}>{outcome}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="filter-row">
            <label>
              Exit Reason
              <select
                value={filters.exit_reason}
                onChange={(event) => setFilters({ ...filters, exit_reason: event.target.value })}
              >
                <option value="all">All</option>
                {options.exitReasons.map((reason) => (
                  <option key={reason} value={reason}>{reason}</option>
                ))}
              </select>
            </label>
            <label>
              Stop Type
              <select
                value={filters.stop_type}
                onChange={(event) => setFilters({ ...filters, stop_type: event.target.value })}
              >
                <option value="all">All</option>
                {options.stopTypes.map((stopType) => (
                  <option key={stopType} value={stopType}>{stopType}</option>
                ))}
              </select>
            </label>
            <button
              className="button ghost"
              onClick={() =>
                setFilters({
                  symbol: "all",
                  strategy: "all",
                  book: "all",
                  outcome: "all",
                  exit_reason: "all",
                  stop_type: "all"
                })
              }
            >
              Reset Filters
            </button>
          </div>
        </div>

        {isLoading && <div className="muted">Loading charts...</div>}
        {!isLoading && (filtered.length || 0) === 0 ? <div className="muted">No charts found.</div> : null}

        <div className="chart-grid">
          {filtered.map((item, idx) => (
            <button
            type="button"
            className="chart-card"
            key={item.filename}
            onClick={() => setSelectedIdx(idx)}
          >
            <div className="chart-name">
              {item.symbol ?? "?"}
              {item.strategy ? ` · ${item.strategy}` : ""}
              {item.outcome ? ` · ${item.outcome}` : ""}
            </div>
            <div className="muted small">
              {item.exit_reason ? `Exit: ${item.exit_reason}` : "Exit: n/a"}
              {item.stop_type_at_exit ? ` · ${item.stop_type_at_exit}` : ""}
            </div>
            <img src={chartUrl(runId, item.filename)} alt={item.filename} loading="lazy" />
          </button>
        ))}
      </div>
    </SectionCard>

    {selected ? (
      <div className="modal-backdrop" onClick={() => setSelectedIdx(null)}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <div className="modal-title">{selected.filename}</div>
            <div className="modal-actions">
              <div className="zoom-controls">
                <button className="button ghost" onClick={zoomOut}>-</button>
                <input
                  type="range"
                  min={0.2}
                  max={10}
                  step={0.1}
                  value={zoom}
                  onChange={(e) => setZoom(clampZoom(Number(e.target.value)))}
                />
                <button className="button ghost" onClick={zoomIn}>+</button>
                <button className="button ghost" onClick={resetZoom}>Reset</button>
                <span className="muted small">{zoom.toFixed(2)}x</span>
              </div>
              <a
                className="button ghost"
                href={chartUrl(runId, selected.filename)}
                target="_blank"
                rel="noreferrer"
              >
                  Open
                </a>
              <button className="button ghost" onClick={() => setSelectedIdx(null)}>
                Close
              </button>
            </div>
          </div>
          <div className="modal-body">
            <div
              className="chart-zoom-wrap"
              onWheel={(e) => {
                e.preventDefault();
                const delta = e.deltaY < 0 ? 0.1 : -0.1;
                setZoom((z) => clampZoom(z + delta));
              }}
            >
              <img
                src={chartUrl(runId, selected.filename)}
                alt={selected.filename}
                className="chart-full"
                loading="lazy"
                style={{ transform: `scale(${zoom})` }}
              />
            </div>
          </div>
          <div className="modal-footer">
            <button
              className="button ghost"
              onClick={() =>
                  setSelectedIdx((prev) =>
                    prev === null ? null : Math.max(0, (prev - 1 + filtered.length) % filtered.length)
                  )
                }
              >
                ← Prev
              </button>
              <button
                className="button ghost"
                onClick={() =>
                  setSelectedIdx((prev) =>
                    prev === null ? null : (prev + 1) % filtered.length
                  )
                }
              >
                Next →
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
