Changelog
=========

Version 0.2.0
-------------

To be released.

- Added basic method dispatching by path pattern (URI template) through
  ``http-resource`` annotation, e.g.,
  ``@http-resource(method="GET", path="/users/{login}/works/{work-id}/")``.
  [`#130`__]
- Added ``allowed_origins`` and ``allowed_headers`` options for CORS_ to
  ``WsgiApp`` constructor.  It supports simple wildcard (``*``) pattern syntax
  as well.
- Added ``AnnotationError``, ``NoJsonError``, and ``ServiceMethodError``
  exceptions.
- ``WsgiApp.url_map`` attribute was gone.
- ``/ping/`` resource was gone.

__ https://github.com/spoqa/nirum/issues/130
.. _CORS: https://www.w3.org/TR/cors/


Version 0.1.0
-------------

Released on July 10, 2017.
