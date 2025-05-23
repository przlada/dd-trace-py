"""
The ``asyncpg`` integration traces database requests made using connection
and cursor objects.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(asyncpg=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.asyncpg['service']

   The service name reported by default for asyncpg connections.

   This option can also be set with the ``DD_ASYNCPG_SERVICE``
   environment variable.

   Default: ``postgres``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

Service
^^^^^^^

To configure the service name used by the asyncpg integration on a per-instance
basis use the ``Pin`` API::

    import asyncpg
    from ddtrace.trace import Pin

    conn = asyncpg.connect("postgres://localhost:5432")
    Pin.override(conn, service="custom-service")
"""
