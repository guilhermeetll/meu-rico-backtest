export type Metrics = {
  total_pnl: number;
  total_return: number | null;
  gross_pnl: number;
  fees: number;
  slippage_cost: number;
  sharpe: number | null;
  deflated_sharpe: number | null;
  max_drawdown: number;
  max_drawdown_pct: number | null;
  n_trades: number;
  win_rate: number | null;
  payoff: number | null;
  net_pnl_after_tax: number;
  tax_paid: number;
  n_sessions: number;
  n_trials: number;
};

export type EquityPoint = {
  date: string;
  equity: number;
  equity_after_tax: number;
  daily_pnl: number;
  daily_pnl_after_tax: number;
  baseline?: boolean;
};

export type TradeRow = {
  session_date: string;
  symbol: string;
  direction: "long" | "short";
  quantity: number;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  gross_pnl: number;
  fees: number;
  slippage_cost: number;
  pnl: number;
  signal_return: number;
};

export type Segment = {
  start: string | null;
  end: string | null;
  metrics: Metrics;
  equity: EquityPoint[];
};

export type WalkForward = {
  n_configurations: number;
  windows: Array<{
    train_start: string;
    train_end: string;
    test_start: string;
    test_end: string;
    chosen_params: Record<string, unknown>;
    train_score: number | null;
    test_pnl: number;
  }>;
  oos_metrics: Metrics;
  oos_equity: EquityPoint[];
};

export type BacktestResult = {
  id: string;
  created_at: string;
  strategy: string;
  symbol: string;
  data_source: string;
  timeframe: string;
  summary: string;
  metrics: Metrics;
  in_sample: Segment | null;
  out_of_sample: Segment | null;
  walk_forward: WalkForward | null;
  equity: EquityPoint[];
  trades: TradeRow[];
  warnings: string[];
  notes: string[];
  request: {
    strategy_params: Record<string, unknown>;
  };
};

export type StudyResult = {
  n_variants: number;
  n_trials: number;
  variants: Array<Omit<BacktestResult, "id" | "created_at">>;
};

export type HistoryItem = {
  id: string;
  created_at: string | null;
  strategy: string;
  symbol: string;
  data_source: string;
  timeframe: string;
  summary: string;
  net_pnl_after_tax: number | null;
  sharpe: number | null;
  n_trades: number | null;
  status: string;
};
