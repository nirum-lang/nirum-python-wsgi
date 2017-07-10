Nirum services as WSGI apps
===========================

.. image:: https://travis-ci.org/spoqa/nirum-python-wsgi.svg?branch=master
   :target: https://travis-ci.org/spoqa/nirum-python-wsgi
   :alt: Build status

.. image:: https://badge.fury.io/py/nirum-wsgi.svg
   :target: https://pypi.org/project/nirum-wsgi/
   :alt: Latest PyPI version

This package provides ``nirum_wsgi.WsgiApp`` class which adapts a given
Nirum service to a WSGI application:

.. code-block:: python

   from youtservice import YourService
   from nirum_wsgi import WsgiApp

   class YourServiceImpl(YourService):
       ...

   app = WsgiApp(YourServiceImpl())

There's a development-purpose CLI launcher named ``nirum-server`` as well:

.. code-block:: bash

   nirum-server -H 0.0.0.0 -p 8080 --debug 'yourserviceimpl:YourServiceImpl()'

.. include:: CHANGES.rst
