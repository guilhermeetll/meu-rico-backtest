from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

from engine.instruments import InstrumentSpec
from engine.models import RawTrade
from engine.series import contracts_of, return_versus_prior_close
from engine.sessions import cash_auction_window, cash_session, session_bounds
from engine.strategies.base import ParamField, Strategy

SIGNAL_ANCHORS = ("session_open", "prior_close")
SIGNAL_ENDS = ("session_open", "cash_open")
TRADE_WINDOWS = ("session_close", "before_cash_auction")


def _clock(value: str) -> time:
    try:
        hour, minute = value.strip().split(":")
        parsed = time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Horário deve estar no formato HH:MM ou auto.") from exc
    return parsed


def _at(day, clock: time, tz) -> pd.Timestamp:
    stamp = pd.Timestamp(datetime.combine(day, clock))
    if tz is None:
        return stamp
    return stamp.tz_localize(tz)


class IntradayMomentumStrategy(Strategy):
    id = "intraday_momentum"
    label = "Momentum intraday"
    description = (
        "O retorno da primeira parte do pregão define a direção: compra se for "
        "maior que o limiar e venda se for menor que o negativo do limiar. "
        "A referência pode ser a abertura do dia ou o fechamento anterior do "
        "mesmo contrato, incluindo o gap. A operação entra no início da janela "
        "escolhida e zera no fim dela."
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
                    "no calendário da B3 (10:30 com os 30 minutos de hoje; 11:30 quando a "
                    "abertura do à vista era 11:00). Vale para as duas referências."
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
                    "leilão de fechamento do à vista. O relógio segue o calendário: 16:25–16:55 "
                    "quando o à vista fecha às 17:00 e 17:25–17:55 quando fecha às 18:00."
                ),
            ),
            ParamField("threshold", "Limiar do retorno", "float", 0.0, step=0.0001,
                       help="Fração. 0,001 = 0,1%. Zero opera qualquer retorno diferente de zero."),
            ParamField("quantity", "Quantidade", "int", 1, min=1, help="Contratos ou ações."),
            ParamField("session_open", "Abertura", "string", "auto",
                       help="HH:MM ou auto para o calendário do ativo."),
            ParamField("session_close", "Fechamento", "string", "auto",
                       help="HH:MM ou auto. Define o fim da operação quando a janela é session_close."),
        ]

    def generate(self, bars, params, instrument: InstrumentSpec, bar_minutes: int):
        resolved = self.resolved_params(params)
        signal_minutes = int(resolved["signal_minutes"])
        trade_minutes = int(resolved["trade_minutes"])
        threshold = float(resolved["threshold"])
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
        open_override = str(resolved["session_open"]).strip().lower()
        close_override = str(resolved["session_close"]).strip().lower()
        bar_delta = timedelta(minutes=bar_minutes)
        warnings: list[str] = []
        trades: list[RawTrade] = []

        frame = bars.sort_values("timestamp")
        tz = frame["timestamp"].dt.tz
        for day, day_bars in frame.groupby(frame["timestamp"].dt.date, sort=True):
            series = contracts_of(day_bars)
            if len(series) > 1:
                warnings.append(
                    f"{day.isoformat()}: o pregão mistura {', '.join(series)}. "
                    "Sinal pulado para não transformar o salto entre contratos em retorno."
                )
                continue
            contract = series[0] if series else _contract_of(day_bars)
            auto_open, auto_close = session_bounds(instrument.family, day, contract)
            open_t = auto_open if open_override in {"", "auto"} else _clock(str(resolved["session_open"]))
            close_t = auto_close if close_override in {"", "auto"} else _clock(str(resolved["session_close"]))
            start = _at(day, open_t, tz)
            session_end = _at(day, close_t, tz)
            cash_open, _, _ = cash_session(day)
            if signal_end_mode == "cash_open":
                signal_end = _at(day, cash_open, tz) + timedelta(minutes=signal_minutes)
            else:
                signal_end = start + timedelta(minutes=signal_minutes)
            if trade_window == "before_cash_auction":
                window_start, window_end = cash_auction_window(day)
                trade_start = _at(day, window_start, tz)
                trade_end = _at(day, window_end, tz)
            else:
                trade_start = session_end - timedelta(minutes=trade_minutes)
                trade_end = session_end
            if trade_start <= signal_end:
                warnings.append(
                    f"{day.isoformat()}: a janela da operação começa antes do fim do sinal. Pregão ignorado."
                )
                continue

            covered_until = session_end if session_end > trade_end else trade_end
            session = day_bars[(day_bars["timestamp"] >= start) & (day_bars["timestamp"] < covered_until)]
            if session.empty:
                continue
            opening = session[session["timestamp"] < signal_end]
            if opening.empty:
                warnings.append(f"{day.isoformat()}: sem negócio na janela do sinal.")
                continue
            open_price = float(opening.iloc[0]["open"])
            if open_price <= 0:
                continue
            closed = session[session["timestamp"] + bar_delta <= signal_end]
            if closed.empty:
                warnings.append(
                    f"{day.isoformat()}: nenhuma barra termina até o fim do sinal. "
                    "Timeframe grosseiro demais para essa janela."
                )
                continue
            signal_price = float(closed.iloc[-1]["close"])
            if signal_anchor == "prior_close":
                if not contract:
                    warnings.append(
                        f"{day.isoformat()}: sem identificação do contrato. "
                        "Sinal pelo fechamento anterior pulado."
                    )
                    continue
                signal_return = return_versus_prior_close(frame, day, contract, signal_price)
                if signal_return is None:
                    warnings.append(
                        f"{day.isoformat()}: sem fechamento anterior de {contract}. "
                        "Sinal pulado para não usar o fechamento de outro vencimento."
                    )
                    continue
            else:
                signal_return = signal_price / open_price - 1.0
            if signal_return > threshold:
                direction = 1
            elif signal_return < -threshold:
                direction = -1
            else:
                continue

            entries = session[(session["timestamp"] >= trade_start) & (session["timestamp"] < trade_end)]
            if entries.empty:
                warnings.append(f"{day.isoformat()}: sem barra na janela de entrada.")
                continue
            entry = entries.iloc[0]
            entry_time = entry["timestamp"].to_pydatetime()
            bar_end = session["timestamp"] + bar_delta
            exits = session[(bar_end <= trade_end) & (bar_end > entry["timestamp"])]
            if exits.empty:
                warnings.append(f"{day.isoformat()}: sem barra de saída depois da entrada.")
                continue
            exit_row = exits.iloc[-1]
            exit_time = (exit_row["timestamp"] + bar_delta).to_pydatetime()
            trades.append(
                RawTrade(
                    session_date=day,
                    direction=direction,
                    quantity=quantity,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=float(entry["open"]),
                    exit_price=float(exit_row["close"]),
                    signal_return=signal_return,
                )
            )
        return trades, _unique(warnings)


def _contract_of(day_bars) -> str | None:
    if "contract" not in day_bars.columns:
        return None
    value = day_bars["contract"].iloc[0]
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
