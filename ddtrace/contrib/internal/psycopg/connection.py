from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import dbapi
from ddtrace.contrib.internal.psycopg.cursor import Psycopg2FetchTracedCursor
from ddtrace.contrib.internal.psycopg.cursor import Psycopg2TracedCursor
from ddtrace.contrib.internal.psycopg.cursor import Psycopg3FetchTracedCursor
from ddtrace.contrib.internal.psycopg.cursor import Psycopg3TracedCursor
from ddtrace.contrib.internal.psycopg.extensions import _patch_extensions
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import sql
from ddtrace.internal.constants import COMPONENT
from ddtrace.trace import Pin


class Psycopg3TracedConnection(dbapi.TracedConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = Psycopg3FetchTracedCursor if config.psycopg.trace_fetch_methods else Psycopg3TracedCursor

        super(Psycopg3TracedConnection, self).__init__(conn, pin, config.psycopg, cursor_cls=cursor_cls)

    def execute(self, *args, **kwargs):
        """Execute a query and return a cursor to read its results."""

        def patched_execute(*args, **kwargs):
            try:
                cur = self.cursor()
                if kwargs.get("binary", None):
                    cur.format = 1  # set to 1 for binary or 0 if not
                return cur.execute(*args, **kwargs)
            except Exception as ex:
                raise ex.with_traceback(None)

        return patched_execute(*args, **kwargs)


class Psycopg2TracedConnection(dbapi.TracedConnection):
    """TracedConnection wraps a Connection with tracing code."""

    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = Psycopg2FetchTracedCursor if config.psycopg.trace_fetch_methods else Psycopg2TracedCursor

        super(Psycopg2TracedConnection, self).__init__(conn, pin, config.psycopg, cursor_cls=cursor_cls)


def patch_conn(conn, traced_conn_cls, pin=None):
    """Wrap will patch the instance so that its queries are traced."""
    # ensure we've patched extensions (this is idempotent) in
    # case we're only tracing some connections.
    _config = None
    if pin:
        extensions_to_patch = pin._config.get("_extensions_to_patch", None)
        _config = pin._config
        if extensions_to_patch:
            _patch_extensions(extensions_to_patch)

    c = traced_conn_cls(conn)

    # if the connection has an info attr, we are using psycopg3
    if hasattr(conn, "dsn"):
        dsn = sql.parse_pg_dsn(conn.dsn)
    else:
        dsn = sql.parse_pg_dsn(conn.info.dsn)

    tags = {
        net.TARGET_HOST: dsn.get("host"),
        net.TARGET_PORT: dsn.get("port", 5432),
        net.SERVER_ADDRESS: dsn.get("host"),
        db.NAME: dsn.get("dbname"),
        db.USER: dsn.get("user"),
        "db.application": dsn.get("application_name"),
        db.SYSTEM: "postgresql",
    }
    Pin(tags=tags, _config=_config).onto(c)
    return c


def patched_connect_factory(psycopg_module):
    def patched_connect(connect_func, _, args, kwargs):
        traced_conn_cls = Psycopg3TracedConnection if psycopg_module.__name__ == "psycopg" else Psycopg2TracedConnection

        pin = Pin.get_from(psycopg_module)

        if not pin or not pin.enabled() or not pin._config.trace_connect:
            conn = connect_func(*args, **kwargs)
        else:
            with pin.tracer.trace(
                "{}.{}".format(connect_func.__module__, connect_func.__name__),
                service=ext_service(pin, pin._config),
                span_type=SpanTypes.SQL,
            ) as span:
                span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
                span.set_tag_str(COMPONENT, pin._config.integration_name)
                if span.get_tag(db.SYSTEM) is None:
                    span.set_tag_str(db.SYSTEM, pin._config.dbms_name)

                span.set_tag(_SPAN_MEASURED_KEY)
                conn = connect_func(*args, **kwargs)

        return patch_conn(conn, pin=pin, traced_conn_cls=traced_conn_cls)

    return patched_connect
