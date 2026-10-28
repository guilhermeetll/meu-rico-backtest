from __future__ import annotations

from datetime import timedelta

from engine.instruments import InstrumentSpec
from engine.models import RawTrade, SkippedSession
from engine.series import contracts_of, return_versus_prior_close
from engine.sessions import cash_auction_window, cash_session, is_ash_wednesday, session_bounds
from engine.strategies.base import ParamField, Strategy
from engine.strategies.common import (
    at as _at,
    clock as _clock,
    contract_of as _contract_of,
    incomplete_window as _incomplete_window,
    span as _span,
    unique as _unique,
)
from engine.trades import as_lookup, price_as_of

BAR_FILL_NOTE = (
    "Preenchimento do momentum: aproximação por barra. "
    "O preço de um horário é o open da barra que começa nele, ou o close da barra que termina nele."
)
TICK_FILL_NOTE = (
    "Preenchimento do momentum: último negócio do tickercsv até o horário. "
    "O sinal, a entrada e a saída usam esse preço."
)

SIGNAL_ANCHORS = ("session_open", "prior_close")
SIGNAL_ENDS = ("session_open", "cash_open")
TRADE_WINDOWS = ("session_close", "before_cash_auction")


class IntradayMomentumStrategy(Strategy):
    id = "intraday_momentum"
    label = "Momentum intraday"
    description = (
        "O retorno da primeira parte do pregão define a direção: compra se for "
        "maior que o limiar e venda se for menor que o negativo do limiar. "
        "A referência pode ser a abertura do dia ou o fechamento anterior do "
        "mesmo contrato, incluindo o gap. A operação entra no início da janela "
        "escolhida e zera no fim dela. Com negócios do tickercsv, o preço de "
        "um horário é o último negócio até esse instante, no sinal e na operação. "
        "Sem esses negócios, o preço fica na barra."
    )

    def param_schema(self) -> list[ParamField]:
        return [
            ParamField("signal_minutes", "Janela do sinal (minutos)", "int", 30, min=1, max=400,
                       help="Minutos até o preço que encerra o sinal."),
            ParamField(
                "signal_anchor",
                "Referência do sinal",
                "string",
                "session_open",
                options=SIGNAL_ANCHORS,
                help=(
                    "session_open mede da abertura até o fim do sinal. "
                    "prior_close mede do fechamento anterior do mesmo contrato até esse "
                    "instante, incluindo o gap noturno (Gao, Han, Li e Zhou, 2018). "
                    "Sem esse fechamento, o sinal é pulado."
                ),
            ),
            ParamField(
                "signal_end",
                "Fim do sinal",
                "string",
                "session_open",
                options=SIGNAL_ENDS,
                help=(
                    "session_open encerra o sinal signal_minutes depois da abertura do ativo. "
                    "cash_open encerra signal_minutes depois da abertura do mercado à vista, "
                    "no calendário da B3 (10:30 com os 30 minutos de hoje; 13:30 na Quarta-feira "
                    "de Cinzas; 11:30 quando a abertura do à vista era 11:00). Vale para as duas referências."
                ),
            ),
            ParamField("trade_minutes", "Janela da operação (minutos)", "int", 30, min=1, max=400,
                       help="Usada quando a janela é session_close: últimos minutos até o fechamento."),
            ParamField(
                "trade_window",
                "Janela da operação",
                "string",
                "session_close",
                options=TRADE_WINDOWS,
                help=(
                    "session_close opera nos últimos trade_minutes até o fechamento configurado. "
                    "before_cash_auction é a meia hora contínua que termina quando começa o "
                    "leilão de fechamento do à vista. De out/2023 a set/2026 isso é 16:25–16:55 "
                    "também no inverno. Na Quarta-feira de Cinzas o leilão começa às 17:55."
                ),
            ),
            ParamField("threshold", "Limiar do retorno", "float", 0.0, step=0.0001,
                       help="Fração. 0,001 = 0,1%. Zero opera qualquer retorno diferente de zero."),
            ParamField("quantity", "Quantidade", "int", 1, min=1, help="Contratos ou ações."),
            ParamField("session_open", "Abertura", "string", "auto",
                       help="HH:MM ou auto para o calendário do ativo."),
            ParamField("session_close", "Fechamento", "string", "auto",
                       help="HH:MM ou auto. Define o fim da operação quando a janela é session_close."),
            ParamField(
                "skip_ash_wednesday",
                "Pular Quarta-feira de Cinzas",
                "bool",
                True,
                help=(
                    "O pregão abre às 13:00. Ligado, o dia é pulado. Desligado, o sinal "
                    "pela abertura do WIN e o sinal pela abertura do à vista usam essa abertura. "
                    "A janela antes do leilão, nesses dias, é 17:25–17:55."
                ),
            ),
            ParamField(
                "min_bar_coverage",
                "Cobertura mínima das janelas",
                "float",
                0.9,
                min=0,
                max=1,
                step=0.05,
                help=(
                    "Fração das barras exigida na janela do sinal e na janela da operação. "
                    "0,9 = 90%. Zero desliga só essa fração: a barra de início e a de fim "
                    "continuam obrigatórias. Abaixo do mínimo, o pregão é pulado."
                ),
            ),
            ParamField(
                "edge_tolerance_minutes",
                "Tolerância da primeira barra (minutos)",
                "int",
                5,
                min=0,
                max=120,
                help=(
                    "A primeira barra de cada janela pode atrasar até esses minutos. "
                    "O leilão de abertura do WIN costuma não ter negócio às 09:00; "
                    "09:02 ou 09:03 ainda conta. Esses minutos iniciais entram na cobertura. "
                    "Uma primeira barra às 11:00, 12:00 ou 15:00 não conta."
                ),
            ),
        ]

    def generate(self, bars, params, instrument: InstrumentSpec, bar_minutes: int, trades=None):
        return self.generate_many(bars, [params], instrument, bar_minutes, as_lookup(trades))[0]

    def generate_many(self, bars, variants: list[dict], instrument: InstrumentSpec, bar_minutes: int, lookup):
        """One pass over the sessions. Every variant shares that day's prints."""
        runs = [self._prepare(params, instrument, bar_minutes, lookup is not None) for params in variants]
        frame = bars.sort_values("timestamp")
        tz = frame["timestamp"].dt.tz
        for day, day_bars in frame.groupby(frame["timestamp"].dt.date, sort=True):
            series = contracts_of(day_bars)
            contract = series[0] if len(series) == 1 else None
            day_trades = None
            if lookup is not None and len(series) <= 1:
                day_trades = lookup.get(day, contract)
                if day_trades is not None and len(day_trades):
                    day_trades = day_trades.sort_values("timestamp", kind="mergesort")
            for run in runs:
                self._session(run, frame, day, day_bars, tz, day_trades)
        return [self._finish(run) for run in runs]

    def _prepare(self, params, instrument: InstrumentSpec, bar_minutes: int, ticks_available: bool) -> dict:
        resolved = self.resolved_params(params)
        signal_minutes = int(resolved["signal_minutes"])
        trade_minutes = int(resolved["trade_minutes"])
        quantity = int(resolved["quantity"])
        signal_anchor = str(resolved["signal_anchor"]).strip().lower()
        signal_end_mode = str(resolved["signal_end"]).strip().lower()
        trade_window = str(resolved["trade_window"]).strip().lower()
        if signal_minutes < 1 or trade_minutes < 1:
            raise ValueError("As janelas do sinal e da operação precisam ter ao menos 1 minuto.")
        if quantity < 1:
            raise ValueError("A quantidade precisa ser pelo menos 1.")
        if signal_anchor not in SIGNAL_ANCHORS:
            raise ValueError("A referência do sinal deve ser session_open ou prior_close.")
        if signal_end_mode not in SIGNAL_ENDS:
            raise ValueError("O fim do sinal deve ser session_open ou cash_open.")
        if trade_window not in TRADE_WINDOWS:
            raise ValueError("A janela da operação deve ser session_close ou before_cash_auction.")
        min_coverage = float(resolved["min_bar_coverage"])
        edge_minutes = int(resolved["edge_tolerance_minutes"])
        if not 0 <= min_coverage <= 1:
            raise ValueError("A cobertura mínima deve estar entre 0 e 1.")
        if edge_minutes < 0:
            raise ValueError("A tolerância da primeira barra não pode ser negativa.")
        return {
            "instrument": instrument,
            "resolved": resolved,
            "signal_minutes": signal_minutes,
            "trade_minutes": trade_minutes,
            "threshold": float(resolved["threshold"]),
            "quantity": quantity,
            "signal_anchor": signal_anchor,
            "signal_end_mode": signal_end_mode,
            "trade_window": trade_window,
            "bar_minutes": bar_minutes,
            "bar_delta": timedelta(minutes=bar_minutes),
            "edge": timedelta(minutes=edge_minutes),
            "edge_minutes": edge_minutes,
            "min_coverage": min_coverage,
            "skip_ash": _as_bool(resolved["skip_ash_wednesday"]),
            "ticks_available": ticks_available,
            "warnings": [],
            "skipped": [],
            "raw": [],
            "saw_ticks": False,
            "used_bars": False,
            "fallback_days": [],
        }

    def _finish(self, run: dict):
        if run["ticks_available"] and run["saw_ticks"]:
            run["warnings"].append(TICK_FILL_NOTE)
        if run["fallback_days"]:
            run["warnings"].append(
                "Preenchimento do momentum: sem negócio individual em "
                + ", ".join(run["fallback_days"])
                + ". Nesses pregões o preço usou a aproximação por barra."
            )
        if run["used_bars"] and not run["saw_ticks"]:
            run["warnings"].append(BAR_FILL_NOTE)
        return run["raw"], _unique(run["warnings"]), run["skipped"]

    def _session(self, run: dict, frame, day, day_bars, tz, day_trades) -> None:
        instrument = run["instrument"]
        resolved = run["resolved"]
        warnings = run["warnings"]
        skipped = run["skipped"]
        signal_minutes = run["signal_minutes"]
        signal_anchor = run["signal_anchor"]
        bar_delta = run["bar_delta"]
        if run["skip_ash"] and is_ash_wednesday(day):
            warnings.append(
                f"{day.isoformat()}: Quarta-feira de Cinzas, abertura às 13:00. "
                "Pregão pulado. Desligue skip_ash_wednesday para operar esse dia."
            )
            return
        series = contracts_of(day_bars)
        if len(series) > 1:
            warnings.append(
                f"{day.isoformat()}: o pregão mistura {', '.join(series)}. "
                "Sinal pulado para não transformar o salto entre contratos em retorno."
            )
            return
        contract = series[0] if series else _contract_of(day_bars)
        auto_open, auto_close = session_bounds(instrument.family, day, contract)
        open_override = str(resolved["session_open"]).strip().lower()
        close_override = str(resolved["session_close"]).strip().lower()
        open_t = auto_open if open_override in {"", "auto"} else _clock(str(resolved["session_open"]))
        close_t = auto_close if close_override in {"", "auto"} else _clock(str(resolved["session_close"]))
        start = _at(day, open_t, tz)
        session_end = _at(day, close_t, tz)
        cash_open, _, _ = cash_session(day)
        if run["signal_end_mode"] == "cash_open":
            signal_end = _at(day, cash_open, tz) + timedelta(minutes=signal_minutes)
        else:
            signal_end = start + timedelta(minutes=signal_minutes)
        if run["trade_window"] == "before_cash_auction":
            window_start, window_end = cash_auction_window(day)
            trade_start = _at(day, window_start, tz)
            trade_end = _at(day, window_end, tz)
        else:
            trade_start = session_end - timedelta(minutes=run["trade_minutes"])
            trade_end = session_end
        if trade_start <= signal_end:
            warnings.append(
                f"{day.isoformat()}: a janela da operação começa antes do fim do sinal. Pregão ignorado."
            )
            return
        signal_window_start = (
            start if signal_anchor == "session_open" else signal_end - timedelta(minutes=signal_minutes)
        )
        gaps = []
        signal_gap = _incomplete_window(
            day_bars["timestamp"], signal_window_start, signal_end, bar_delta, run["edge"],
            run["min_coverage"], run["bar_minutes"], run["edge_minutes"],
        )
        if signal_gap:
            gaps.append(f"Janela do sinal {_span(signal_window_start, signal_end)}: {signal_gap}")
        trade_gap = _incomplete_window(
            day_bars["timestamp"], trade_start, trade_end, bar_delta, run["edge"],
            run["min_coverage"], run["bar_minutes"], run["edge_minutes"],
        )
        if trade_gap:
            gaps.append(f"Janela da operação {_span(trade_start, trade_end)}: {trade_gap}")
        if gaps:
            warnings.append(f"{day.isoformat()}: pregão pulado. {'. '.join(gaps)}.")
            if signal_gap:
                skipped.append(SkippedSession(
                    session_date=day,
                    window="signal",
                    reason=f"Janela do sinal {_span(signal_window_start, signal_end)}: {signal_gap}.",
                ))
            if trade_gap:
                skipped.append(SkippedSession(
                    session_date=day,
                    window="trade",
                    reason=f"Janela da operação {_span(trade_start, trade_end)}: {trade_gap}.",
                ))
            return

        if day_trades is not None and len(day_trades):
            run["saw_ticks"] = True
        else:
            run["used_bars"] = True
            if run["ticks_available"]:
                run["fallback_days"].append(day.isoformat())

        covered_until = session_end if session_end > trade_end else trade_end
        session = day_bars[(day_bars["timestamp"] >= start) & (day_bars["timestamp"] < covered_until)]
        if session.empty:
            return
        opening = session[session["timestamp"] < signal_end]
        if opening.empty:
            warnings.append(f"{day.isoformat()}: sem negócio na janela do sinal.")
            return
        open_price = float(opening.iloc[0]["open"])
        if open_price <= 0:
            return
        closed = session[session["timestamp"] + bar_delta <= signal_end]
        if closed.empty:
            warnings.append(
                f"{day.isoformat()}: nenhuma barra termina até o fim do sinal. "
                "Timeframe grosseiro demais para essa janela."
            )
            return
        signal_price = float(closed.iloc[-1]["close"])
        if day_trades is not None and len(day_trades):
            opened = price_as_of(day_trades, start, not_before=start)
            if opened is not None and opened > 0:
                open_price = opened
            marked = price_as_of(day_trades, signal_end, not_before=start)
            if marked is not None and marked > 0:
                signal_price = marked
        if signal_anchor == "prior_close":
            if not contract:
                warnings.append(
                    f"{day.isoformat()}: sem identificação do contrato. "
                    "Sinal pelo fechamento anterior pulado."
                )
                return
            signal_return = return_versus_prior_close(frame, day, contract, signal_price)
            if signal_return is None:
                warnings.append(
                    f"{day.isoformat()}: sem fechamento anterior de {contract}. "
                    "Sinal pulado para não usar o fechamento de outro vencimento."
                )
                return
        else:
            signal_return = signal_price / open_price - 1.0
        threshold = run["threshold"]
        if signal_return > threshold:
            direction = 1
        elif signal_return < -threshold:
            direction = -1
        else:
            return

        entries = session[(session["timestamp"] >= trade_start) & (session["timestamp"] < trade_end)]
        if entries.empty:
            warnings.append(f"{day.isoformat()}: sem barra na janela de entrada.")
            return
        entry = entries.iloc[0]
        entry_time = entry["timestamp"].to_pydatetime()
        entry_price = float(entry["open"])
        bar_end = session["timestamp"] + bar_delta
        exits = session[(bar_end <= trade_end) & (bar_end > entry["timestamp"])]
        if exits.empty:
            warnings.append(f"{day.isoformat()}: sem barra de saída depois da entrada.")
            return
        exit_row = exits.iloc[-1]
        exit_time = (exit_row["timestamp"] + bar_delta).to_pydatetime()
        exit_price = float(exit_row["close"])
        if day_trades is not None and len(day_trades):
            entered = price_as_of(day_trades, trade_start, not_before=start)
            if entered is not None and entered > 0:
                entry_price = entered
            exited = price_as_of(day_trades, trade_end, not_before=start)
            if exited is not None and exited > 0:
                exit_price = exited
        run["raw"].append(
            RawTrade(
                session_date=day,
                direction=direction,
                quantity=run["quantity"],
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                signal_return=signal_return,
            )
        )


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "sim", "on"}:
        return True
    if text in {"0", "false", "no", "nao", "não", "off"}:
        return False
    raise ValueError("Pular a Quarta-feira de Cinzas deve ser verdadeiro ou falso.")

