"""Small helpers used to defer cross-domain calls and avoid import cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable


def service_call(module_name: str, attribute: str) -> Callable[..., Any]:
    """Resolve a cross-domain service function only when it is invoked."""

    def invoke(*args: Any, **kwargs: Any) -> Any:
        return getattr(import_module(module_name), attribute)(*args, **kwargs)

    invoke.__name__ = attribute
    return invoke

