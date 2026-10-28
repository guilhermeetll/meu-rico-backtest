from engine.strategies.gap import GapReversalStrategy
from engine.strategies.momentum import IntradayMomentumStrategy
from engine.strategies.orb import OpeningRangeBreakoutStrategy

STRATEGIES: dict[str, type] = {
    "intraday_momentum": IntradayMomentumStrategy,
    "gap_reversal": GapReversalStrategy,
    "opening_range_breakout": OpeningRangeBreakoutStrategy,
}


def get_strategy(strategy_id: str):
    try:
        cls = STRATEGIES[strategy_id]
    except KeyError as exc:
        known = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"Estratégia desconhecida: {strategy_id}. Disponíveis: {known}.") from exc
    return cls()


def list_strategies() -> list[dict]:
    items = []
    for cls in STRATEGIES.values():
        strategy = cls()
        items.append(
            {
                "id": strategy.id,
                "label": strategy.label,
                "description": strategy.description,
                "params": [
                    {
                        "name": field.name,
                        "label": field.label,
                        "type": field.type,
                        "default": field.default,
                        "help": field.help,
                        "min": field.min,
                        "max": field.max,
                        "step": field.step,
                        "options": list(field.options),
                    }
                    for field in strategy.param_schema()
                ],
            }
        )
    return items
