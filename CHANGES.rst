Changelog
=========

Version 0.2.1
-------------

To be released.


Version 0.2.0
-------------

Released on February 4, 2018.

- Made parameters having an optional type possible to be omitted. [`#205`_]
- Added method dispatching by querystring pattern
  e.g., ``@http-resource(method="GET", path="/users?from={from}&to={to}")``.
  [`#130`_]
- Added basic method dispatching by path pattern (URI template) through
  ``http-resource`` annotation, e.g.,
  ``@http-resource(method="GET", path="/users/{login}/works/{work-id}/")``.
  [`#130`_]
- Added ``allowed_origins`` and ``allowed_headers`` options for CORS_ to
  ``WsgiApp`` constructor.  It supports simple wildcard (``*``) pattern syntax
  as well.
- Added ``AnnotationError``, ``NoJsonError``, and ``ServiceMethodError``
  exceptions.
- Since returning a disallowed value which does not match to the return type
  is the fault the server-side made, the HTTP status code for the case became
  ``500 Internal Server Error`` instead of ``400 Bad Request``.
  Also now it writes logs using ``logging`` module.
- For the mistakes returning ``None`` from a method having non-null return type,
  now it became to show a more readable and debug-friendly message with a proper
  response instead of uncaught Python exception.
- ``WsgiApp.url_map`` attribute was gone.
- ``/ping/`` resource was gone.
- Fixed ``nirum-server`` command able to import a Python module/package from
  the current working directory (``.``; CWD).

.. _#205: https://github.com/spoqa/nirum/issues/205
.. _#130: https://github.com/spoqa/nirum/issues/130
.. _CORS: https://www.w3.org/TR/cors/


Version 0.1.0
-------------

Released on July 10, 2017.
