export interface RunInfo {
  run_id: string;
  path: string;
  created_at: string;
  date_range?: { start: string; end: string } | null;
  files: string[];
}

export interface FilterOptions {
  date_range: { min: string; max: string } | null;
  books: string[];
  strategies: string[];
  symbols: string[];
  exit_reasons: string[];
  stop_types: string[];
}

export interface ChartInfo {
  filename: string;
  size_bytes: number;
  chart_type?: string;
  symbol?: string;
  strategy?: string;
  book?: string;
  position_id?: number;
  entry_id?: number;
  entry_date?: string;
  exit_date?: string;
  holding_days?: number;
  exit_reason?: string;
  stop_type_at_exit?: string;
  realized_pnl?: number;
  return_pct?: number;
  outcome?: string;
}

export interface DailyActivityResponse {
  as_of_date: string;
  opened: PositionSummary[];
  closed: PositionSummary[];
}

export interface PositionSummary {
  position_id: number;
  symbol: string;
  book: string;
  strategy: string;
  entry_date: string;
  exit_date: string;
  entry_price: number | null;
  exit_price: number | null;
  holding_days: number | null;
  exit_reason: string;
  stop_type_at_exit: string;
  realized_pnl: number | null;
  exit_r: number | null;
  giveback_r: number | null;
}

export interface EquityPoint {
  date: string;
  total: number;
  core: number;
  convex: number;
}

export interface OverviewKpis {
  total_return_pct?: number;
  cagr_pct?: number;
  annualized_volatility_pct?: number;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  calmar_ratio?: number;
  max_drawdown_pct?: number;
  time_in_market_pct?: number;
  turnover_pct?: number;
  total_trades?: number;
  win_rate_pct?: number;
  avg_win?: number;
  avg_loss?: number;
  profit_factor?: number;
  avg_holding_days?: number;
  avg_trade_return_pct?: number;
  payoff_ratio?: number;
  expectancy?: number;
  total_pnl?: number;
  total_commission?: number;
  total_slippage_cost?: number;
}

export interface OverviewResponse {
  kpis: Record<string, OverviewKpis>;
  equity: EquityPoint[];
  drawdown: EquityPoint[];
  monthly_returns: { month: string; return_pct: number }[];
}

export interface AllocatorResponse {
  state_timeline: { date: string; book: string; state: string }[];
  rejections: { date: string; book: string; reason: string; count: number }[];
  acceptance_rate: { date: string; book: string; acceptance_rate: number }[];
  events: {
    date: string;
    book: string;
    prev_state: string;
    new_state: string;
    trigger: string;
    book_drawdown_pct: number;
    portfolio_drawdown_pct: number;
    risk_budget_before: number;
    risk_budget_after: number;
  }[];
}

export interface RiskResponse {
  exposure: { date: string; core: number; convex: number }[];
  open_risk: { date: string; core: number; convex: number }[];
  open_positions: { date: string; core: number; convex: number }[];
  top_contributors: { symbol: string; total_pnl: number; trades: number }[];
}

export interface AttributionResponse {
  strategy_leaderboard: {
    book: string;
    strategy: string;
    trades: number;
    win_rate_pct: number;
    total_pnl: number;
    avg_pnl: number;
    avg_win: number;
    avg_loss: number;
    profit_factor: number;
    avg_holding_days: number;
    avg_trade_return_pct: number;
    payoff_ratio: number;
    expectancy: number;
    pct_peak_r_ge_1: number;
    pct_exit_ge_0_5r_given_peak_1: number;
  }[];
  pnl_by_strategy: { strategy: string; book: string; total_pnl: number }[];
  exit_reason_attribution: {
    book: string;
    reason: string;
    stop_type_at_exit: string;
    count: number;
    win_rate_pct: number;
    total_pnl: number;
    avg_exit_r: number;
    avg_peak_r: number;
    avg_giveback_r: number;
    strategy: string;
  }[];
}

export interface ExitsResponse {
  exit_reason: { book: string; reason: string; stop_type_at_exit: string; count: number; total_pnl: number }[];
  giveback_distribution: { bin_start: number; bin_end: number; count: number }[];
  winner_retention: { peak_bin: string; exit_bin: string; count: number }[];
  worst_givebacks: {
    position_id: number;
    symbol: string;
    book: string;
    strategy: string;
    entry_date: string;
    exit_date: string;
    giveback_r: number;
    peak_r: number;
    exit_r: number;
    realized_pnl: number;
  }[];
}

export interface PositionsResponse {
  total: number;
  rows: {
    position_id: number;
    book: string;
    strategy: string;
    symbol: string;
    entry_date: string;
    exit_date: string;
    holding_days: number;
    initial_risk: number;
    mfe_r: number;
    mae_r: number;
    peak_r: number;
    exit_r: number;
    giveback_r: number;
    exit_reason: string;
    stop_type_at_exit: string;
    realized_pnl: number;
  }[];
}

export interface PositionDetailResponse {
  position: {
    position_id: number;
    entry_id: number;
    book: string;
    strategy: string;
    symbol: string;
    entry_date: string;
    exit_date: string;
    entry_price: number;
    exit_price: number;
    holding_days: number;
    initial_risk: number;
    adds_taken: number;
    mfe_r: number;
    mae_r: number;
    peak_r: number;
    exit_r: number;
    giveback_r: number;
    exit_reason: string;
    stop_type_at_exit: string;
    realized_pnl: number;
    return_pct: number;
    costs_commission: number;
    costs_slippage: number;
    allocator_state_at_entry: string;
    allocator_state_at_exit: string;
    risk_budget_pct_at_entry: number;
    risk_budget_dollars_at_entry: number;
  } | null;
  trades: {
    date: string;
    symbol: string;
    action: string;
    qty: number;
    price: number;
    exec_price: number;
    commission: number;
    slippage_cost: number;
    book: string;
    allocator_state: string;
    reason: string;
    exit_r: number;
    peak_r: number;
    giveback_r: number;
    stop_type_at_exit: string;
    add_on: string;
    add_count: number;
  }[];
}
