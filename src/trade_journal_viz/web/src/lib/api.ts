import type {
  RunInfo,
  FilterOptions,
  ChartInfo,
  DailyActivityResponse,
  OverviewResponse,
  AllocatorResponse,
  RiskResponse,
  AttributionResponse,
  ExitsResponse,
  PositionsResponse,
  PositionDetailResponse
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000/api";

export interface FilterQuery {
  start_date?: string;
  end_date?: string;
  book?: string[];
  strategy?: string[];
  symbol?: string[];
  allocator_state?: string[];
  exit_reason?: string[];
  stop_type?: string[];
}

function buildQuery(params: FilterQuery & { offset?: string; limit?: string }): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (!value || value.length === 0) {
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, item));
      return;
    }
    search.set(key, value);
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`API error ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchRuns(): Promise<RunInfo[]> {
  return apiGet<RunInfo[]>("/runs");
}

export function fetchRunMetadata(runId: string): Promise<RunInfo> {
  return apiGet<RunInfo>(`/runs/${runId}/metadata`);
}

export function fetchFilterOptions(runId: string): Promise<FilterOptions> {
  return apiGet<FilterOptions>(`/runs/${runId}/filters`);
}

export function fetchCharts(runId: string): Promise<ChartInfo[]> {
  return apiGet<ChartInfo[]>(`/runs/${runId}/charts`);
}

export function chartUrl(runId: string, filename: string): string {
  return `${API_BASE}/runs/${runId}/charts/${encodeURIComponent(filename)}`;
}

export function fetchDailyActivity(runId: string): Promise<DailyActivityResponse> {
  return apiGet<DailyActivityResponse>(`/runs/${runId}/daily-activity`);
}

export function fetchOverview(runId: string, filters: FilterQuery): Promise<OverviewResponse> {
  return apiGet<OverviewResponse>(`/runs/${runId}/overview${buildQuery(filters)}`);
}

export function fetchAllocator(runId: string, filters: FilterQuery): Promise<AllocatorResponse> {
  return apiGet<AllocatorResponse>(`/runs/${runId}/allocator${buildQuery(filters)}`);
}

export function fetchRisk(runId: string, filters: FilterQuery): Promise<RiskResponse> {
  return apiGet<RiskResponse>(`/runs/${runId}/risk${buildQuery(filters)}`);
}

export function fetchAttribution(runId: string, filters: FilterQuery): Promise<AttributionResponse> {
  return apiGet<AttributionResponse>(`/runs/${runId}/attribution${buildQuery(filters)}`);
}

export function fetchExits(runId: string, filters: FilterQuery): Promise<ExitsResponse> {
  return apiGet<ExitsResponse>(`/runs/${runId}/exits${buildQuery(filters)}`);
}

export function fetchPositions(
  runId: string,
  filters: FilterQuery,
  offset = 0,
  limit = 200
): Promise<PositionsResponse> {
  const query = buildQuery({ ...filters, offset: String(offset), limit: String(limit) });
  return apiGet<PositionsResponse>(`/runs/${runId}/journal/positions${query}`);
}

export function fetchPositionDetail(runId: string, positionId: number): Promise<PositionDetailResponse> {
  return apiGet<PositionDetailResponse>(`/runs/${runId}/journal/positions/${positionId}`);
}
