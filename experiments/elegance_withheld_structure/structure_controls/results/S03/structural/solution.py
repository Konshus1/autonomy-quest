"""Composable Decorator-based order pricing in integer cents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol


PriceResult = dict[str, Any]


class Pricer(Protocol):
    def __call__(self, order: Any) -> PriceResult:
        ...


def _require_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    return value


def _copy_result(result: Any) -> PriceResult:
    if type(result) is not dict or set(result) != {"cents", "trace"}:
        raise TypeError("a pricer must return exactly 'cents' and 'trace'")

    cents = result["cents"]
    trace = result["trace"]
    if type(cents) is not int:
        raise TypeError("result cents must be an int")
    if type(trace) is not list or any(type(item) is not str for item in trace):
        raise TypeError("result trace must be a list of strings")

    return {"cents": cents, "trace": list(trace)}


def base_pricer(order: Any) -> PriceResult:
    """Return an order's subtotal without modifying the order."""
    cents = _require_int(order["subtotal_cents"], "subtotal_cents")
    return {"cents": cents, "trace": []}


class _Wrapper:
    """Base Decorator: delegates to one inner pricer."""

    def __init__(self, inner: Pricer) -> None:
        if not callable(inner):
            raise TypeError("inner pricer must be callable")
        self._inner = inner

    def _inner_result(self, order: Any) -> PriceResult:
        return _copy_result(self._inner(order))


class _DiscountWrapper(_Wrapper):
    def __init__(self, inner: Pricer, basis_points: int) -> None:
        super().__init__(inner)
        self._basis_points = basis_points

    def __call__(self, order: Any) -> PriceResult:
        result = self._inner_result(order)
        return {
            "cents": result["cents"] * (10000 - self._basis_points) // 10000,
            "trace": list(result["trace"]),
        }


class _TaxWrapper(_Wrapper):
    def __init__(self, inner: Pricer, basis_points: int) -> None:
        super().__init__(inner)
        self._basis_points = basis_points

    def __call__(self, order: Any) -> PriceResult:
        result = self._inner_result(order)
        return {
            "cents": result["cents"] * (10000 + self._basis_points) // 10000,
            "trace": list(result["trace"]),
        }


class _FeeWrapper(_Wrapper):
    def __init__(self, inner: Pricer, cents: int) -> None:
        super().__init__(inner)
        self._cents = cents

    def __call__(self, order: Any) -> PriceResult:
        result = self._inner_result(order)
        return {
            "cents": result["cents"] + self._cents,
            "trace": list(result["trace"]),
        }


class _AuditWrapper(_Wrapper):
    def __init__(self, inner: Pricer, label: str) -> None:
        super().__init__(inner)
        self._label = label

    def __call__(self, order: Any) -> PriceResult:
        result = self._inner_result(order)
        return {
            "cents": result["cents"],
            "trace": [*result["trace"], self._label],
        }


@dataclass(frozen=True)
class _Capability:
    kind: str
    value: Any

    def __call__(self, inner: Pricer) -> Pricer:
        if self.kind == "discount":
            return _DiscountWrapper(inner, self.value)
        if self.kind == "tax":
            return _TaxWrapper(inner, self.value)
        if self.kind == "fee":
            return _FeeWrapper(inner, self.value)
        if self.kind == "audit":
            return _AuditWrapper(inner, self.value)
        raise ValueError(f"unknown capability kind: {self.kind!r}")


def discount(basis_points: int) -> _Capability:
    return _Capability("discount", _require_int(basis_points, "basis_points"))


def tax(basis_points: int) -> _Capability:
    return _Capability("tax", _require_int(basis_points, "basis_points"))


def fee(cents: int) -> _Capability:
    return _Capability("fee", _require_int(cents, "cents"))


def audit(label: str) -> _Capability:
    if type(label) is not str:
        raise TypeError("label must be a str")
    return _Capability("audit", label)


def make_pricer(pricer: Pricer, capabilities: Iterable[_Capability]) -> Pricer:
    """Wrap a pricer with unique capabilities in the supplied list order."""
    if not callable(pricer):
        raise TypeError("pricer must be callable")

    try:
        requested = list(capabilities)
    except TypeError as exc:
        raise TypeError("capabilities must be iterable") from exc

    if any(type(capability) is not _Capability for capability in requested):
        raise TypeError("unknown capability")

    wrapped = pricer
    seen: set[_Capability] = set()
    for capability in requested:
        if capability not in seen:
            seen.add(capability)
            wrapped = capability(wrapped)
    return wrapped
