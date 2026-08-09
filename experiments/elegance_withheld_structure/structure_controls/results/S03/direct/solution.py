"""Composable order pricing using the Decorator pattern."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


PriceResult = dict[str, Any]
Pricer = Callable[[Mapping[str, Any]], PriceResult]


def _require_int(value: object, name: str, *, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def _copy_result(result: object) -> PriceResult:
    if not isinstance(result, Mapping):
        raise TypeError("a pricer must return a mapping")

    try:
        cents = result["cents"]
        trace = result["trace"]
    except KeyError as exc:
        raise ValueError("a pricer result must contain cents and trace") from exc

    if type(cents) is not int:
        raise TypeError("a pricer's cents value must be an integer")
    if not isinstance(trace, list) or any(not isinstance(item, str) for item in trace):
        raise TypeError("a pricer's trace value must be a list of strings")

    return {"cents": cents, "trace": list(trace)}


def base_pricer(order: Mapping[str, Any]) -> PriceResult:
    cents = order["subtotal_cents"]
    if type(cents) is not int:
        raise TypeError("subtotal_cents must be an integer")
    return {"cents": cents, "trace": []}


class PricerDecorator(ABC):
    """Base Decorator sharing the same callable contract as a pricer."""

    def __init__(self, inner: Pricer) -> None:
        if not callable(inner):
            raise TypeError("inner pricer must be callable")
        self._inner = inner

    def __call__(self, order: Mapping[str, Any]) -> PriceResult:
        result = _copy_result(self._inner(order))
        return self._apply(result)

    @abstractmethod
    def _apply(self, result: PriceResult) -> PriceResult:
        raise NotImplementedError


class _DiscountDecorator(PricerDecorator):
    def __init__(self, inner: Pricer, basis_points: int) -> None:
        super().__init__(inner)
        self._basis_points = basis_points

    def _apply(self, result: PriceResult) -> PriceResult:
        cents = result["cents"] * (10000 - self._basis_points) // 10000
        return {"cents": cents, "trace": list(result["trace"])}


class _TaxDecorator(PricerDecorator):
    def __init__(self, inner: Pricer, basis_points: int) -> None:
        super().__init__(inner)
        self._basis_points = basis_points

    def _apply(self, result: PriceResult) -> PriceResult:
        cents = result["cents"] * (10000 + self._basis_points) // 10000
        return {"cents": cents, "trace": list(result["trace"])}


class _FeeDecorator(PricerDecorator):
    def __init__(self, inner: Pricer, cents: int) -> None:
        super().__init__(inner)
        self._cents = cents

    def _apply(self, result: PriceResult) -> PriceResult:
        return {
            "cents": result["cents"] + self._cents,
            "trace": list(result["trace"]),
        }


class _AuditDecorator(PricerDecorator):
    def __init__(self, inner: Pricer, label: str) -> None:
        super().__init__(inner)
        self._label = label

    def _apply(self, result: PriceResult) -> PriceResult:
        trace = list(result["trace"])
        trace.append(self._label)
        return {"cents": result["cents"], "trace": trace}


class _Capability(ABC):
    def __call__(self, inner: Pricer) -> PricerDecorator:
        if not callable(inner):
            raise TypeError("inner pricer must be callable")
        return self.wrap(inner)

    @abstractmethod
    def wrap(self, inner: Pricer) -> PricerDecorator:
        raise NotImplementedError


@dataclass(frozen=True)
class _Discount(_Capability):
    basis_points: int

    def wrap(self, inner: Pricer) -> PricerDecorator:
        return _DiscountDecorator(inner, self.basis_points)


@dataclass(frozen=True)
class _Tax(_Capability):
    basis_points: int

    def wrap(self, inner: Pricer) -> PricerDecorator:
        return _TaxDecorator(inner, self.basis_points)


@dataclass(frozen=True)
class _Fee(_Capability):
    cents: int

    def wrap(self, inner: Pricer) -> PricerDecorator:
        return _FeeDecorator(inner, self.cents)


@dataclass(frozen=True)
class _Audit(_Capability):
    label: str

    def wrap(self, inner: Pricer) -> PricerDecorator:
        return _AuditDecorator(inner, self.label)


def discount(basis_points: int) -> _Capability:
    return _Discount(_require_int(basis_points, "basis_points", maximum=10000))


def tax(basis_points: int) -> _Capability:
    return _Tax(_require_int(basis_points, "basis_points"))


def fee(cents: int) -> _Capability:
    return _Fee(_require_int(cents, "cents"))


def audit(label: str) -> _Capability:
    if not isinstance(label, str):
        raise TypeError("label must be a string")
    return _Audit(label)


def make_pricer(pricer: Pricer, capabilities: Iterable[_Capability]) -> Pricer:
    if not callable(pricer):
        raise TypeError("pricer must be callable")

    try:
        requested = list(capabilities)
    except TypeError as exc:
        raise TypeError("capabilities must be iterable") from exc

    result: Pricer = pricer
    seen: set[_Capability] = set()
    for capability in requested:
        if not isinstance(capability, _Capability):
            raise TypeError(f"unknown capability: {capability!r}")
        if capability in seen:
            continue
        seen.add(capability)
        result = capability.wrap(result)

    return result
