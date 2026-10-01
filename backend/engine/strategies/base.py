from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from engine.instruments import InstrumentSpec
from engine.models import RawTrade, SkippedSession


@dataclass(frozen=True)
class ParamField:
    name: str
    label: str
    type: str
    default: object
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()


class Strategy(ABC):
    id: str
    label: str
    description: str

    @abstractmethod
    def param_schema(self) -> list[ParamField]:
        raise NotImplementedError

    def resolved_params(self, params: dict | None) -> dict:
        resolved = {field.name: field.default for field in self.param_schema()}
        for key, value in (params or {}).items():
            if key in resolved or key.startswith("_"):
                resolved[key] = value
        return resolved

    @abstractmethod
    def generate(
        self,
        bars,
        params: dict,
        instrument: InstrumentSpec,
        bar_minutes: int,
    ) -> tuple[list[RawTrade], list[str], list[SkippedSession]]:
        raise NotImplementedError
