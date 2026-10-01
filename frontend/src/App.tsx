import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getBacktest, listBacktests, runBacktest, runStudy, uploadCsv } from "./api";
import type { BacktestResult, HistoryItem, Metrics, StudyResult } from "./types";

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const price = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

const SOURCE_LABEL: Record<string, string> = {
  csv: "CSV",
  b3: "B3",
  yahoo: "Yahoo Finance",
};

function moneyClass(value: number): string {
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "";
}

function formatRatio(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return num.format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return pct.format(value);
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "America/Sao_Paulo",
  });
}

function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
}

function variantLabel(params: Record<string, unknown>): string {
  const anchor = params.signal_anchor === "prior_close" ? "Fechamento anterior" : "Abertura do pregão";
  const windowName = params.trade_window === "before_cash_auction" ? "16:25–16:55" : "Até o fechamento";
  return `${anchor} · ${windowName}`;
}

function MetricCards({ metrics }: { metrics: Metrics }) {
  const items = [
    ["Retorno total", formatPercent(metrics.total_return), moneyClass(metrics.total_pnl)],
    ["Resultado líquido de IR", brl.format(metrics.net_pnl_after_tax), moneyClass(metrics.net_pnl_after_tax)],
    ["Sharpe", formatRatio(metrics.sharpe), ""],
    ["Sharpe deflacionado", formatPercent(metrics.deflated_sharpe), ""],
    ["Drawdown máximo", brl.format(metrics.max_drawdown), "neg"],
    ["Drawdown %", formatPercent(metrics.max_drawdown_pct), ""],
    ["Operações", String(metrics.n_trades), ""],
    ["Taxa de acerto", formatPercent(metrics.win_rate), ""],
    ["Payoff", formatRatio(metrics.payoff), ""],
    ["IR pago", brl.format(metrics.tax_paid), ""],
    ["Custos", brl.format(metrics.fees), ""],
    ["Slippage", brl.format(metrics.slippage_cost), ""],
  ] as const;
  return (
    <div className="cards">
      {items.map(([label, value, tone]) => (
        <div className="card" key={label}>
          <span>{label}</span>
          <strong className={tone}>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function EquityChart({ points }: { points: BacktestResult["equity"] }) {
  const data = points.map((point) => ({
    ...point,
    label: point.baseline ? "Início" : point.date.slice(5),
  }));
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#2c3828" vertical={false} />
          <XAxis dataKey="label" stroke="#b7c0ad" fontSize={12} />
          <YAxis stroke="#b7c0ad" fontSize={12} tickFormatter={(value) => num.format(Number(value))} width={72} />
          <Tooltip
            formatter={(value, name) => [brl.format(Number(value)), name]}
            contentStyle={{ background: "#181e16", border: "1px solid #2c3828" }}
          />
          <Legend />
          <Line type="monotone" dataKey="equity" name="Capital" stroke="#e0a45a" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="equity_after_tax" name="Líquido de IR" stroke="#8fce78" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function App() {
  const [symbol, setSymbol] = useState("WIN");
  const [dataSource, setDataSource] = useState("csv");
  const [timeframe, setTimeframe] = useState("1min");
  const [start, setStart] = useState("2026-09-17");
  const [end, setEnd] = useState("2026-09-30");
  const [useExample, setUseExample] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [columnMap, setColumnMap] = useState("");
  const [signalMinutes, setSignalMinutes] = useState(30);
  const [signalAnchor, setSignalAnchor] = useState("session_open");
  const [tradeMinutes, setTradeMinutes] = useState(30);
  const [tradeWindow, setTradeWindow] = useState("session_close");
  const [threshold, setThreshold] = useState(0);
  const [quantity, setQuantity] = useState(1);
  const [sessionOpen, setSessionOpen] = useState("auto");
  const [sessionClose, setSessionClose] = useState("auto");
  const [fee, setFee] = useState(0.5);
  const [feePercent, setFeePercent] = useState(0);
  const [slippage, setSlippage] = useState(1);
  const [tax, setTax] = useState(20);
  const [capital, setCapital] = useState(10000);
  const [nTrials, setNTrials] = useState(1);
  const [includeAfterHours, setIncludeAfterHours] = useState(false);
  const [splitEnabled, setSplitEnabled] = useState(true);
  const [splitFraction, setSplitFraction] = useState(0.7);
  const [splitDate, setSplitDate] = useState("");
  const [wfEnabled, setWfEnabled] = useState(false);
  const [train, setTrain] = useState(6);
  const [testSize, setTestSize] = useState(3);
  const [step, setStep] = useState("");
  const [anchored, setAnchored] = useState(false);
  const [metric, setMetric] = useState("sharpe");
  const [thresholds, setThresholds] = useState("");
  const [running, setRunning] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [study, setStudy] = useState<StudyResult | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const costFamily = symbol.toUpperCase().startsWith("WIN") ? "WIN" : "EQUITY";
  const [appliedFamily, setAppliedFamily] = useState("WIN");

  async function refreshHistory() {
    try {
      setHistory(await listBacktests());
    } catch {
      setHistory([]);
    }
  }

  useEffect(() => {
    void refreshHistory();
  }, []);

  useEffect(() => {
    if (costFamily === appliedFamily) return;
    setAppliedFamily(costFamily);
    if (costFamily === "WIN") {
      setFee(0.5);
      setFeePercent(0);
      setSlippage(1);
    } else {
      setFee(0);
      setFeePercent(0.023);
      setSlippage(1);
    }
  }, [appliedFamily, costFamily]);

  const timeframes = useMemo(() => {
    if (dataSource === "yahoo") return ["5min", "60min"];
    return ["1min", "5min", "60min"];
  }, [dataSource]);

  useEffect(() => {
    if (!timeframes.includes(timeframe)) setTimeframe(timeframes[0]);
  }, [timeframes, timeframe]);

  async function buildRequest() {
    let csvSource = "example";
    if (dataSource === "csv" && !useExample) {
      if (!file) throw new Error("Escolha um CSV ou use o arquivo de exemplo.");
      csvSource = await uploadCsv(file);
    }
    let parsedMap: Record<string, string> | null = null;
    if (columnMap.trim()) {
      const parsed = JSON.parse(columnMap) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("O mapeamento de colunas precisa ser um objeto JSON.");
      }
      parsedMap = parsed as Record<string, string>;
    }
    const gridValues = thresholds
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
      .map(Number);
    return {
      strategy: "intraday_momentum",
      symbol,
      data_source: dataSource,
      timeframe,
      start,
      end,
      csv_source: csvSource,
      column_map: parsedMap,
      strategy_params: {
        signal_minutes: signalMinutes,
        signal_anchor: signalAnchor,
        trade_minutes: tradeMinutes,
        trade_window: tradeWindow,
        threshold,
        quantity,
        session_open: sessionOpen,
        session_close: sessionClose,
      },
      costs: { fee_per_side: fee, slippage_ticks: slippage, fee_rate: feePercent / 100 },
      tax_rate: tax / 100,
      initial_capital: capital,
      n_trials: nTrials,
      include_after_hours: includeAfterHours,
      sample_split: {
        enabled: splitEnabled,
        in_sample_fraction: splitFraction,
        split_date: splitDate || null,
      },
      walk_forward: {
        enabled: wfEnabled,
        train_sessions: train,
        test_sessions: testSize,
        step_sessions: step ? Number(step) : null,
        anchored,
        optimize_metric: metric,
        param_grid: gridValues.length ? { threshold: gridValues } : null,
      },
    };
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setRunning(true);
    setError("");
    try {
      const created = await runBacktest(await buildRequest());
      setResult(created);
      await refreshHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível rodar o backtest.");
    } finally {
      setRunning(false);
    }
  }

  async function onCompare() {
    setComparing(true);
    setError("");
    try {
      const payload = await buildRequest();
      setStudy(await runStudy({
        ...payload,
        variants: [
          { signal_anchor: "session_open", trade_window: "session_close" },
          { signal_anchor: "session_open", trade_window: "before_cash_auction" },
          { signal_anchor: "prior_close", trade_window: "session_close" },
          { signal_anchor: "prior_close", trade_window: "before_cash_auction" },
        ],
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível comparar as variantes.");
    } finally {
      setComparing(false);
    }
  }

  async function openHistory(id: string) {
    setError("");
    try {
      setResult(await getBacktest(id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível abrir o histórico.");
    }
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1 className="brand">Meu Rico <span>Backtest</span></h1>
          <p className="lede">
            Simulação de day trade na B3. A primeira estratégia é o momentum intraday do WIN:
            a primeira meia hora define a direção e a operação acontece na última meia hora.
          </p>
        </div>
        <div className="badge">Nenhuma ordem é enviada</div>
      </header>
      <div className="layout">
        <form className="panel" onSubmit={onSubmit}>
          <h2>Configuração</h2>
          <fieldset>
            <legend>Dados</legend>
            <label>
              Ativo
              <input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} />
            </label>
            <label>
              Fonte
              <select value={dataSource} onChange={(event) => setDataSource(event.target.value)}>
                <option value="csv">CSV de barras (exemplo incluso)</option>
                <option value="b3">Negócios da B3</option>
                <option value="yahoo">Yahoo Finance (ações)</option>
              </select>
            </label>
            <div className="grid-2">
              <label>
                Início
                <input type="date" value={start} onChange={(event) => setStart(event.target.value)} required />
              </label>
              <label>
                Fim
                <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} required />
              </label>
            </div>
            <label>
              Timeframe
              <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
                {timeframes.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>
            {dataSource === "csv" && (
              <>
                <label className="inline">
                  <input type="checkbox" checked={useExample} onChange={(event) => setUseExample(event.target.checked)} />
                  Usar o CSV de exemplo do WINV26
                </label>
                {!useExample && (
                  <label>
                    Arquivo
                    <input type="file" accept=".csv,.txt" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
                  </label>
                )}
                <label>
                  Mapeamento de colunas (JSON, opcional)
                  <textarea
                    value={columnMap}
                    placeholder='{"Abertura": "open", "Fechamento": "close"}'
                    onChange={(event) => setColumnMap(event.target.value)}
                  />
                </label>
              </>
            )}
            {dataSource === "b3" && (
              <>
                <p className="hint">
                  Lê {"{B3_DATA_ROOT}/{TICKER}/{AAAA-MM-DD}.zip"} no formato original. WIN contínuo usa a série de win_ativo.csv em B3_META_ROOT, um vencimento por pregão. O salto entre contratos não vira retorno.
                </p>
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={includeAfterHours}
                    onChange={(event) => setIncludeAfterHours(event.target.checked)}
                  />
                  Incluir after-market (sessão 6)
                </label>
              </>
            )}
          </fieldset>
          <fieldset>
            <legend>Estratégia</legend>
            <label>
              Referência do sinal
              <select value={signalAnchor} onChange={(event) => setSignalAnchor(event.target.value)}>
                <option value="session_open">Abertura do pregão até o fim da primeira janela</option>
                <option value="prior_close">Fechamento anterior do mesmo contrato, com o gap</option>
              </select>
            </label>
            <p className="hint">
              O fechamento anterior é o de Gao, Han, Li e Zhou (2018). Se esse pregão do mesmo contrato não existir, o sinal é pulado.
            </p>
            <label>
              Janela da operação
              <select value={tradeWindow} onChange={(event) => setTradeWindow(event.target.value)}>
                <option value="session_close">Últimos minutos até o fechamento</option>
                <option value="before_cash_auction">16:25–16:55, antes do leilão do à vista</option>
              </select>
            </label>
            <div className="grid-2">
              <label>
                Sinal (min)
                <input type="number" min={1} value={signalMinutes} onChange={(event) => setSignalMinutes(Number(event.target.value))} />
              </label>
              <label>
                Operação (min)
                <input type="number" min={1} value={tradeMinutes} onChange={(event) => setTradeMinutes(Number(event.target.value))} />
              </label>
            </div>
            <div className="grid-2">
              <label>
                Limiar
                <input type="number" step="0.0001" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} />
              </label>
              <label>
                Quantidade
                <input type="number" min={1} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
              </label>
            </div>
            <div className="grid-2">
              <label>
                Abertura
                <input value={sessionOpen} onChange={(event) => setSessionOpen(event.target.value)} />
              </label>
              <label>
                Fechamento
                <input value={sessionClose} onChange={(event) => setSessionClose(event.target.value)} />
              </label>
            </div>
            {tradeWindow === "before_cash_auction" ? (
              <p className="hint">A operação entra às 16:25 e zera às 16:55. Abertura, fechamento e minutos continuam valendo na opção até o fechamento.</p>
            ) : (
              <p className="hint">A entrada é no início dos últimos minutos e a saída é no fechamento informado, ou no horário automático do ativo.</p>
            )}
          </fieldset>
          <fieldset>
            <legend>Custos, slippage e IR</legend>
            <div className="grid-2">
              <label>
                Custo fixo por lado (R$)
                <input type="number" min={0} step="0.01" value={fee} onChange={(event) => setFee(Number(event.target.value))} />
              </label>
              <label>
                Taxa por lado (%)
                <input type="number" min={0} step="0.001" value={feePercent} onChange={(event) => setFeePercent(Number(event.target.value))} />
              </label>
            </div>
            <div className="grid-2">
              <label>
                Slippage (ticks)
                <input type="number" min={0} step="1" value={slippage} onChange={(event) => setSlippage(Number(event.target.value))} />
              </label>
            </div>
            <div className="grid-2">
              <label>
                IR day trade (%)
                <input type="number" min={0} max={100} step="1" value={tax} onChange={(event) => setTax(Number(event.target.value))} />
              </label>
              <label>
                Capital de referência
                <input type="number" min={1} step="1" value={capital} onChange={(event) => setCapital(Number(event.target.value))} />
              </label>
            </div>
            <label>
              Configurações testadas (N do Sharpe deflacionado)
              <input type="number" min={1} value={nTrials} onChange={(event) => setNTrials(Number(event.target.value))} />
            </label>
          </fieldset>
          <fieldset>
            <legend>Amostra</legend>
            <label className="inline">
              <input type="checkbox" checked={splitEnabled} onChange={(event) => setSplitEnabled(event.target.checked)} />
              Separar dentro e fora da amostra
            </label>
            <div className="grid-2">
              <label>
                Fração dentro da amostra
                <input type="number" min={0.05} max={0.95} step={0.05} value={splitFraction} onChange={(event) => setSplitFraction(Number(event.target.value))} />
              </label>
              <label>
                Corte por data
                <input type="date" value={splitDate} onChange={(event) => setSplitDate(event.target.value)} />
              </label>
            </div>
            <label className="inline">
              <input type="checkbox" checked={wfEnabled} onChange={(event) => setWfEnabled(event.target.checked)} />
              Walk-forward
            </label>
            {wfEnabled && (
              <>
                <div className="grid-2">
                  <label>
                    Treino (pregões)
                    <input type="number" min={1} value={train} onChange={(event) => setTrain(Number(event.target.value))} />
                  </label>
                  <label>
                    Teste (pregões)
                    <input type="number" min={1} value={testSize} onChange={(event) => setTestSize(Number(event.target.value))} />
                  </label>
                </div>
                <div className="grid-2">
                  <label>
                    Passo
                    <input value={step} placeholder="igual ao teste" onChange={(event) => setStep(event.target.value)} />
                  </label>
                  <label>
                    Métrica do treino
                    <select value={metric} onChange={(event) => setMetric(event.target.value)}>
                      <option value="sharpe">Sharpe</option>
                      <option value="total_pnl">Resultado</option>
                      <option value="net_pnl">Líquido de IR</option>
                    </select>
                  </label>
                </div>
                <label className="inline">
                  <input type="checkbox" checked={anchored} onChange={(event) => setAnchored(event.target.checked)} />
                  Treino ancorado no início
                </label>
                <label>
                  Limiares testados no treino
                  <input value={thresholds} placeholder="0, 0.0005, 0.001" onChange={(event) => setThresholds(event.target.value)} />
                </label>
              </>
            )}
          </fieldset>
          <button className="run" type="submit" disabled={running || comparing}>
            {running ? "Rodando backtest…" : "Rodar backtest"}
          </button>
          <button className="secondary" type="button" onClick={() => void onCompare()} disabled={running || comparing}>
            {comparing ? "Comparando variantes…" : "Comparar sinal e janela"}
          </button>
          <p className="hint">
            WIN: R$ 0,50 por contrato por lado e 1 tick (5 pontos = R$ 1,00). Ação: 0,023% por lado sobre o valor negociado e 1 tick de R$ 0,01. A comparação usa as quatro combinações e o Sharpe deflacionado de cada uma leva o total de variantes.
          </p>
        </form>
        <section className="panel">
          <h2>Resultado</h2>
          {error && <div className="error">{error}</div>}
          {!result && !error && (
            <p className="empty">
              Rode o exemplo do WINV26 para ver métricas, curva de capital e a lista de operações. O arquivo de exemplo já está no repositório e está marcado como exemplo.
            </p>
          )}
          {result && (
            <>
              <p className="summary">{result.summary}</p>
              <MetricCards metrics={result.metrics} />
              <EquityChart points={result.equity} />
              {result.in_sample && result.out_of_sample && (
                <>
                  <h3 className="subhead">Dentro da amostra ({result.in_sample.start} a {result.in_sample.end})</h3>
                  <MetricCards metrics={result.in_sample.metrics} />
                  <h3 className="subhead">Fora da amostra ({result.out_of_sample.start} a {result.out_of_sample.end})</h3>
                  <MetricCards metrics={result.out_of_sample.metrics} />
                </>
              )}
              {result.walk_forward && (
                <>
                  <h3 className="subhead">Walk-forward · {result.walk_forward.n_configurations} configurações</h3>
                  <MetricCards metrics={result.walk_forward.oos_metrics} />
                  <EquityChart points={result.walk_forward.oos_equity} />
                  <div className="scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Treino</th>
                          <th>Teste</th>
                          <th>Limiar</th>
                          <th>Nota</th>
                          <th>Resultado do teste</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.walk_forward.windows.map((window) => (
                          <tr key={`${window.test_start}-${window.test_end}`}>
                            <td>{window.train_start} – {window.train_end}</td>
                            <td>{window.test_start} – {window.test_end}</td>
                            <td>{String(window.chosen_params.threshold ?? "—")}</td>
                            <td>{formatRatio(window.train_score)}</td>
                            <td className={moneyClass(window.test_pnl)}>{brl.format(window.test_pnl)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              <h3 className="subhead">Operações</h3>
              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Pregão</th>
                      <th>Lado</th>
                      <th>Entrada</th>
                      <th>Saída</th>
                      <th>Preço entrada</th>
                      <th>Preço saída</th>
                      <th>Bruto</th>
                      <th>Custos</th>
                      <th>Slippage</th>
                      <th>Resultado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((trade) => (
                      <tr key={`${trade.session_date}-${trade.entry_time}`}>
                        <td>{trade.session_date}</td>
                        <td>{trade.direction === "long" ? "Compra" : "Venda"}</td>
                        <td>{clock(trade.entry_time)}</td>
                        <td>{clock(trade.exit_time)}</td>
                        <td>{price.format(trade.entry_price)}</td>
                        <td>{price.format(trade.exit_price)}</td>
                        <td className={moneyClass(trade.gross_pnl)}>{brl.format(trade.gross_pnl)}</td>
                        <td>{brl.format(trade.fees)}</td>
                        <td>{brl.format(trade.slippage_cost)}</td>
                        <td className={moneyClass(trade.pnl)}>{brl.format(trade.pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {result.warnings.length > 0 && (
                <ul className="warn">
                  {result.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                </ul>
              )}
              <h3 className="subhead">Premissas</h3>
              <ul className="notes">
                {result.notes.map((note) => <li key={note}>{note}</li>)}
              </ul>
            </>
          )}
        </section>
      </div>
      {study && (
        <section className="panel history">
          <h2>Estudo · {study.n_variants} variantes</h2>
          <p className="hint">
            Sharpe deflacionado de cada linha usa N = {study.n_trials}, o total de configurações deste estudo.
          </p>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Variante</th>
                  <th>Operações</th>
                  <th>Líquido de IR</th>
                  <th>Sharpe</th>
                  <th>Sharpe deflacionado</th>
                  <th>N</th>
                </tr>
              </thead>
              <tbody>
                {study.variants.map((item) => (
                  <tr key={variantLabel(item.request.strategy_params)}>
                    <td>{variantLabel(item.request.strategy_params)}</td>
                    <td>{item.metrics.n_trades}</td>
                    <td className={moneyClass(item.metrics.net_pnl_after_tax)}>{brl.format(item.metrics.net_pnl_after_tax)}</td>
                    <td>{formatRatio(item.metrics.sharpe)}</td>
                    <td>{formatPercent(item.metrics.deflated_sharpe)}</td>
                    <td>{item.metrics.n_trials}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <section className="panel history">
        <h2>Histórico</h2>
        {history.length === 0 && <p className="empty">Nenhuma execução gravada ainda.</p>}
        <div className="history-list">
          {history.map((item) => (
            <button
              key={item.id}
              className={result?.id === item.id ? "active" : ""}
              type="button"
              onClick={() => void openHistory(item.id)}
            >
              <strong>{item.symbol}</strong> · {SOURCE_LABEL[item.data_source] ?? item.data_source} · {item.timeframe}
              <small>
                {when(item.created_at)} · líquido {item.net_pnl_after_tax === null ? "—" : brl.format(item.net_pnl_after_tax)} · Sharpe {formatRatio(item.sharpe)} · {item.n_trades ?? 0} operações
              </small>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
