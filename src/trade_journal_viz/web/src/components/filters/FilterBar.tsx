import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { fetchFilterOptions } from "../../lib/api";
import { useFilters } from "../../state/useFilters";

export default function FilterBar() {
  const { runId = "" } = useParams();
  const { filters, setFilters, clearFilters } = useFilters();
  const { data: options } = useQuery({
    queryKey: ["filter-options", runId],
    queryFn: () => fetchFilterOptions(runId),
    enabled: Boolean(runId)
  });

  const bookValue = filters.book?.[0] || "all";
  const allocatorValue = filters.allocator_state?.[0] || "all";
  const strategyValue = filters.strategy?.[0] || "all";
  const symbolValue = filters.symbol?.[0] || "all";
  const exitReasonValue = filters.exit_reason?.[0] || "all";
  const stopTypeValue = filters.stop_type?.[0] || "all";

  useEffect(() => {
    if (!options?.date_range) {
      return;
    }
    const { min, max } = options.date_range;
    const needsReset =
      !filters.start_date ||
      !filters.end_date ||
      (filters.start_date < min || filters.end_date > max);
    if (!needsReset) {
      return;
    }
    setFilters({
      ...filters,
      start_date: min,
      end_date: max
    });
  }, [options, filters, setFilters]);

  return (
    <div className="filter-bar">
      <div className="filter-row">
        <label>
          Start
          <input
            type="date"
            min={options?.date_range?.min}
            max={options?.date_range?.max}
            value={filters.start_date || ""}
            onChange={(event) =>
              setFilters({
                ...filters,
                start_date: event.target.value || undefined
              })
            }
          />
        </label>
        <label>
          End
          <input
            type="date"
            min={options?.date_range?.min}
            max={options?.date_range?.max}
            value={filters.end_date || ""}
            onChange={(event) =>
              setFilters({
                ...filters,
                end_date: event.target.value || undefined
              })
            }
          />
        </label>
        <label>
          Book
          <select
            value={bookValue}
            onChange={(event) =>
              setFilters({
                ...filters,
                book: event.target.value === "all" ? undefined : [event.target.value]
              })
            }
          >
            <option value="all">All</option>
            <option value="core">Core</option>
            <option value="convex">Convex</option>
          </select>
        </label>
        <label>
          Allocator
          <select
            value={allocatorValue}
            onChange={(event) =>
              setFilters({
                ...filters,
                allocator_state: event.target.value === "all" ? undefined : [event.target.value]
              })
            }
          >
            <option value="all">All</option>
            <option value="normal">Normal</option>
            <option value="throttled">Throttled</option>
            <option value="disabled">Disabled</option>
          </select>
        </label>
        <label>
          Stop Type
          <select
            value={stopTypeValue}
            onChange={(event) =>
              setFilters({
                ...filters,
                stop_type: event.target.value === "all" ? undefined : [event.target.value]
              })
            }
          >
            <option value="all">All</option>
            <option value="initial">Initial</option>
            <option value="signal">Signal</option>
            <option value="trailing">Trailing</option>
            <option value="none">None</option>
          </select>
        </label>
      </div>
      <div className="filter-row">
        <label>
          Strategy
          <select
            value={strategyValue}
            onChange={(event) =>
              setFilters({
                ...filters,
                strategy: event.target.value === "all" ? undefined : [event.target.value]
              })
            }
          >
            <option value="all">All</option>
            {options?.strategies?.map((strategy) => (
              <option key={strategy} value={strategy}>
                {strategy}
              </option>
            ))}
          </select>
        </label>
        <label>
          Symbol
          <select
            value={symbolValue}
            onChange={(event) =>
              setFilters({
                ...filters,
                symbol: event.target.value === "all" ? undefined : [event.target.value]
              })
            }
          >
            <option value="all">All</option>
            {options?.symbols?.map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
          </select>
        </label>
        <label>
          Exit Reason
          <select
            value={exitReasonValue}
            onChange={(event) =>
              setFilters({
                ...filters,
                exit_reason: event.target.value === "all" ? undefined : [event.target.value]
              })
            }
          >
            <option value="all">All</option>
            {options?.exit_reasons?.map((reason) => (
              <option key={reason} value={reason}>
                {reason}
              </option>
            ))}
          </select>
        </label>
        <button className="button ghost" onClick={clearFilters}>
          Reset Filters
        </button>
      </div>
    </div>
  );
}
