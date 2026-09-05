import { useSearchParams } from "react-router-dom";
import type { FilterQuery } from "../lib/api";

function parseMulti(params: URLSearchParams, key: string): string[] | undefined {
  const values = params.getAll(key).filter(Boolean);
  return values.length ? values : undefined;
}

export function useFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters: FilterQuery = {
    start_date: searchParams.get("start_date") || undefined,
    end_date: searchParams.get("end_date") || undefined,
    book: parseMulti(searchParams, "book"),
    strategy: parseMulti(searchParams, "strategy"),
    symbol: parseMulti(searchParams, "symbol"),
    allocator_state: parseMulti(searchParams, "allocator_state"),
    exit_reason: parseMulti(searchParams, "exit_reason"),
    stop_type: parseMulti(searchParams, "stop_type")
  };

  function setFilters(next: FilterQuery) {
    const params = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => {
      if (!value || value.length === 0) {
        return;
      }
      if (Array.isArray(value)) {
        value.forEach((item) => params.append(key, item));
      } else {
        params.set(key, value);
      }
    });
    setSearchParams(params, { replace: true });
  }

  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
  }

  return { filters, setFilters, clearFilters };
}
