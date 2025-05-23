"""
This file implements the Core API, the abstraction layer between Integrations and Product code.
The Core API enables two primary use cases: maintaining a tree of ``ExecutionContext`` objects
and dispatching events.

When using the Core API, keep concerns separate between Products and Integrations. Integrations
should not contain any code that references Products (Tracing, AppSec, Spans, WAF, Debugging, et cetera)
and Product code should never reference the library being integrated with (for example by importing ``flask``).

It's helpful to think of the context tree as a Trace with extra data on each Span. It's similar
to a tree of Spans in that it represents the parts of the execution state that Datadog products
care about.

This example shows how ``core.context_with_data`` might be used to create a node in this context tree::


    import flask


    def _patched_request(pin, wrapped, args, kwargs):
        with core.context_with_data(
            "flask._patched_request",
            pin=pin,
            flask_request=flask.request,
            block_request_callable=_block_request_callable,
        ) as ctx, ctx.span:
            return wrapped(*args, **kwargs)


This example looks a bit like a span created by ``tracer.trace()``: it has a name, a ``Pin`` instance, and
``__enter__`` and ``__exit__`` functionality as a context manager. In fact, it's so similar to a span that
the Tracing code in ``ddtrace/tracing`` can create a span directly from it (that's what ``flask_request_call``
is in this example).

The ``ExecutionContext`` object in this example also holds some data that you wouldn't typically find on
spans, like ``flask_request`` and ``block_request_callable``. These illustrate the context's utility as a
generic container for data that Datadog products need related to the current execution. ``block_request_callable``
happens to be used in ``ddtrace/appsec`` by the AppSec product code to make request-blocking decisions, and
``flask_request`` is a reference to a library-specific function that Tracing uses.

The first argument to ``context_with_data`` is the unique name of the context. When choosing this name,
consider how to differentiate it from other similar contexts while making its purpose clear. An easy default
is to use the name of the function within which ``context_with_data`` is being called, prefixed with the
integration name and a dot, for example ``flask._patched_request``.

The integration code finds all of the library-specific objects that products need, and puts them into
the context tree it's building via ``context_with_data``. Product code then accesses the data it needs
by calling ``ExecutionContext.get_item`` like this::


    pin = ctx.get_item("pin")
    current_span = pin.tracer.current_span()
    ctx.set_item("current_span", current_span)
    flask_config = ctx.get_item("flask_config")
    _set_request_tags(ctx.get_item("flask_request"), current_span, flask_config)


Integration code can also call ``get_item`` when necessary, for example when the Flask integration checks
the request blocking flag that may have been set on the context by AppSec code and then runs Flask-specific
response logic::


    if core.get_item(HTTP_REQUEST_BLOCKED):
        result = start_response("403", [("content-type", "application/json")])


In order for ``get_item`` calls in Product code like ``ddtrace/appsec`` to find what they're looking for,
they need to happen at the right time. That's the problem that the ``core.dispatch`` and ``core.on``
functions solve.

The common pattern is that integration code generates events by calling ``dispatch`` and product code
listens to those events by calling ``on``. This allows product code to react to integration code at the
appropriate moments while maintaining clear separation of concerns.

For example, the Flask integration calls ``dispatch`` to indicate that a blocked response just started,
passing some data along with the event::


    call = tracer.trace("operation")
    core.dispatch("flask.blocked_request_callable", call)


The AppSec code listens for this event and does some AppSec-specific stuff in the handler::


    def _on_flask_blocked_request():
        core.set_item(HTTP_REQUEST_BLOCKED, True)
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)


``ExecutionContexts`` also generate their own start and end events that Product code can respond to
like this::


    def _on_jsonify_context_started_flask(ctx):
        span = ctx.get_item("pin").tracer.trace(ctx.get_item("name"))
        ctx.set_item("flask_jsonify_call", span)
    core.on("context.started.flask.jsonify", _on_jsonify_context_started_flask)


The names of these events follow the pattern ``context.[started|ended].<context_name>``.
"""

from contextlib import AbstractContextManager
import logging
import sys
import types
import typing
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.vendor.debtcollector import deprecate

from ..utils.deprecations import DDTraceDeprecationWarning
from . import event_hub  # noqa:F401
from .event_hub import EventResultDict  # noqa:F401
from .event_hub import dispatch
from .event_hub import dispatch_with_results  # noqa:F401
from .event_hub import has_listeners  # noqa:F401
from .event_hub import on  # noqa:F401
from .event_hub import reset as reset_listeners  # noqa:F401


if typing.TYPE_CHECKING:
    from ddtrace._trace.span import Span  # noqa:F401

import contextvars


tracer = None

log = logging.getLogger(__name__)


ROOT_CONTEXT_ID = "__root"
SPAN_DEPRECATION_MESSAGE = (
    "The 'span' keyword argument on ExecutionContext methods is deprecated and will be removed in a future version."
)
SPAN_DEPRECATION_SUGGESTION = (
    "Please store contextual data on the ExecutionContext object using other kwargs and/or set_item()"
)
DEPRECATION_MEMO = set()


def _deprecate_span_kwarg(span):
    if (
        span is not None
        and id(_CURRENT_CONTEXT) not in DEPRECATION_MEMO
        # https://github.com/tiangolo/fastapi/pull/10876
        and "fastapi" not in sys.modules
        and "fastapi.applications" not in sys.modules
    ):
        DEPRECATION_MEMO.add(id(_CURRENT_CONTEXT))
        deprecate(
            SPAN_DEPRECATION_MESSAGE,
            message=SPAN_DEPRECATION_SUGGESTION,
            category=DDTraceDeprecationWarning,
        )


class ExecutionContext(AbstractContextManager):
    def __init__(
        self, identifier: str, parent: Optional["ExecutionContext"] = None, span: Optional["Span"] = None, **kwargs
    ) -> None:
        _deprecate_span_kwarg(span)
        self.identifier: str = identifier
        self._data: Dict[str, Any] = {}
        self._span: Optional["Span"] = span
        self._suppress_exceptions: List[type] = []
        self._data.update(kwargs)
        self._parent: Optional["ExecutionContext"] = parent
        self._inner_span: Optional["Span"] = None

    def __enter__(self) -> "ExecutionContext":
        if self._span is None and "_CURRENT_CONTEXT" in globals():
            self._token: contextvars.Token["ExecutionContext"] = _CURRENT_CONTEXT.set(self)
        dispatch("context.started.%s" % self.identifier, (self,))
        dispatch("context.started.start_span.%s" % self.identifier, (self,))
        return self

    def __repr__(self) -> str:
        return f"{self.__class__.__name__} '{self.identifier}' @ {id(self)}"

    @property
    def parent(self) -> Optional["ExecutionContext"]:
        return self._parent

    @parent.setter
    def parent(self, value: "ExecutionContext") -> None:
        if self._parent is not None:
            raise ValueError("Cannot overwrite ExecutionContext parent")
        self._parent = value

    def __exit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[types.TracebackType]
    ) -> bool:
        dispatch("context.ended.%s" % self.identifier, (self,))
        if self._span is None:
            try:
                if hasattr(self, "_token"):
                    _CURRENT_CONTEXT.reset(self._token)
            except ValueError:
                log.debug(
                    "Encountered ValueError during core contextvar reset() call. "
                    "This can happen when a span holding an executioncontext is "
                    "finished in a Context other than the one that started it."
                )
            except LookupError:
                log.debug(
                    "Encountered LookupError during core contextvar reset() call. I don't know why this is possible."
                )
        if id(self) in DEPRECATION_MEMO:
            DEPRECATION_MEMO.remove(id(self))

        return (
            True
            if exc_type is None
            else any(issubclass(exc_type, exc_type_) for exc_type_ in self._suppress_exceptions)
        )

    def get_item(self, data_key: str, default: Optional[Any] = None) -> Any:
        # NB mimic the behavior of `ddtrace.internal._context` by doing lazy inheritance
        current: Optional[ExecutionContext] = self
        while current is not None:
            if data_key in current._data:
                return current._data.get(data_key)
            current = current.parent
        return default

    def get_local_item(self, data_key: str, default: Optional[Any] = None) -> Any:
        return self._data.get(data_key, default)

    def __getitem__(self, key: str):
        value = self.get_item(key)
        if value is None and key not in self._data:
            raise KeyError
        return value

    def get_items(self, data_keys: List[str]) -> List[Optional[Any]]:
        return [self.get_item(key) for key in data_keys]

    def set_item(self, data_key: str, data_value: Optional[Any]) -> None:
        self._data[data_key] = data_value

    def set_safe(self, data_key: str, data_value: Optional[Any]) -> None:
        if data_key in self._data:
            raise ValueError("Cannot overwrite ExecutionContext data key '%s'", data_key)
        return self.set_item(data_key, data_value)

    def set_items(self, keys_values: Dict[str, Optional[Any]]) -> None:
        for data_key, data_value in keys_values.items():
            self.set_item(data_key, data_value)

    def discard_item(self, data_key: str) -> None:
        # NB mimic the behavior of `ddtrace.internal._context` by doing lazy inheritance
        current: Optional[ExecutionContext] = self
        while current is not None:
            if data_key in current._data:
                del current._data[data_key]
                return
            current = current.parent

    def discard_local_item(self, data_key: str) -> None:
        self._data.pop(data_key, None)

    def root(self):
        if self.identifier == ROOT_CONTEXT_ID:
            return self
        current = self
        while current.parent is not None:
            current = current.parent
        return current

    @property
    def span(self) -> "Span":
        if self._inner_span is None:
            log.warning("No span found in ExecutionContext %s", self.identifier)
            self._inner_span = tracer.current_span() or tracer.trace("default")  # type: ignore
        return self._inner_span

    @span.setter
    def span(self, value: "Span") -> None:
        self._inner_span = value
        if "span_key" in self._data:
            self._data[self._data["span_key"]] = value


def __getattr__(name):
    if name == "root":
        return _CURRENT_CONTEXT.get().root()
    if name == "current":
        return _CURRENT_CONTEXT.get()
    raise AttributeError


_CURRENT_CONTEXT = contextvars.ContextVar("ExecutionContext_var", default=ExecutionContext(ROOT_CONTEXT_ID))
_CONTEXT_CLASS = ExecutionContext


def _reset_context():
    """private function to reset the context. Only used in testing"""
    global _CURRENT_CONTEXT
    _CURRENT_CONTEXT = contextvars.ContextVar("ExecutionContext_var", default=ExecutionContext(ROOT_CONTEXT_ID))


def context_with_data(identifier, parent=None, **kwargs):
    return _CONTEXT_CLASS(identifier, parent=(parent or _CURRENT_CONTEXT.get()), **kwargs)


def add_suppress_exception(exc_type: type) -> None:
    _CURRENT_CONTEXT.get()._suppress_exceptions.append(exc_type)


def get_item(data_key: str, span: Optional["Span"] = None) -> Any:
    _deprecate_span_kwarg(span)
    if span is not None and span._local_root is not None:
        return span._local_root._get_ctx_item(data_key)
    return _CURRENT_CONTEXT.get().get_item(data_key)


def get_local_item(data_key: str, span: Optional["Span"] = None) -> Any:
    return _CURRENT_CONTEXT.get().get_local_item(data_key)


def get_items(data_keys: List[str], span: Optional["Span"] = None) -> List[Optional[Any]]:
    _deprecate_span_kwarg(span)
    if span is not None and span._local_root is not None:
        return [span._local_root._get_ctx_item(key) for key in data_keys]
    return _CURRENT_CONTEXT.get().get_items(data_keys)


def set_safe(data_key: str, data_value: Optional[Any]) -> None:
    _CURRENT_CONTEXT.get().set_safe(data_key, data_value)


# NB Don't call these set_* functions from `ddtrace.contrib`, only from product code!
def set_item(data_key: str, data_value: Optional[Any], span: Optional["Span"] = None) -> None:
    _deprecate_span_kwarg(span)
    if span is not None and span._local_root is not None:
        span._local_root._set_ctx_item(data_key, data_value)
    else:
        _CURRENT_CONTEXT.get().set_item(data_key, data_value)


def set_items(keys_values: Dict[str, Optional[Any]], span: Optional["Span"] = None) -> None:
    _deprecate_span_kwarg(span)
    if span is not None and span._local_root is not None:
        span._local_root._set_ctx_items(keys_values)
    else:
        _CURRENT_CONTEXT.get().set_items(keys_values)


def discard_item(data_key: str) -> None:
    _CURRENT_CONTEXT.get().discard_item(data_key)


def discard_local_item(data_key: str) -> None:
    _CURRENT_CONTEXT.get().discard_local_item(data_key)


def get_span() -> Optional["Span"]:
    current: Optional[ExecutionContext] = _CURRENT_CONTEXT.get()
    while current is not None:
        if current._inner_span is not None:
            return current._inner_span
        current = current.parent
    return None


def get_root_span() -> Optional["Span"]:
    span = get_span()
    if span is None:
        return None if tracer is None else tracer.current_root_span()
    return span._local_root or span
