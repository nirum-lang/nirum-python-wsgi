# -*- coding: utf-8 -*-
""":mod:`nirum_wsgi` --- Nirum services as WSGI apps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import argparse
import collections
import itertools
import json
import logging
import os
import re
import sys
import typing

from nirum._compat import get_union_types, is_union_type
from nirum.datastructures import List
from nirum.deserialize import deserialize_meta
from nirum.serialize import serialize_meta
from nirum.service import Service
from six import integer_types, text_type
from six.moves import http_client, reduce
from six.moves.urllib import parse as urlparse
from werkzeug.http import HTTP_STATUS_CODES
from werkzeug.serving import run_simple

__version__ = '0.4.0'
__all__ = (
    'AnnotationError', 'InvalidJsonError',
    'MethodArgumentError', 'MethodDispatch', 'MethodDispatchError',
    'PathMatch', 'ServiceMethodError',
    'UriTemplateMatchResult', 'UriTemplateMatcher',
    'WsgiApp',
    'is_optional_type', 'match_request', 'parse_json_payload',
)
MethodDispatch = collections.namedtuple('MethodDispatch', [
    'request', 'routed', 'service_method',
    'payload', 'cors_headers'
])
PathMatch = collections.namedtuple('PathMatch', [
    'match_group', 'verb', 'method_name'
])
UriTemplateRule = collections.namedtuple('UriTemplateRule', [
    'uri_template', 'matcher', 'verb', 'name'
])


class Request(object):

    def __init__(self, environ):
        self.environ = environ
        self._raw = None

    @property
    def method(self):
        return self.environ['REQUEST_METHOD']

    @property
    def path(self):
        return self.environ['PATH_INFO']

    @property
    def stream(self):
        return self.environ['wsgi.input']

    @property
    def raw(self):
        if self._raw is None:
            self._raw = self.stream.read()
        return self._raw

    @property
    def text(self):
        return self.raw.decode('utf-8')

    @property
    def querystring(self):
        return self.environ['QUERY_STRING']

    @property
    def args(self):
        return {
            key: value[0] if len(value) == 1 else value
            for key, value in urlparse.parse_qs(self.querystring).items()
        }

    @property
    def headers(self):
        header_prefix = 'HTTP_'

        def normalize(x):
            header = x[len(header_prefix):]
            return '-'.join(h[0] + h[1:].lower() for h in header.split('_'))

        return {
            normalize(k): v
            for k, v in self.environ.items()
            if k.startswith(header_prefix)
        }


class Response(object):

    def __init__(self, content, status_code, headers, **kwargs):
        self.content = content
        self.status_code = status_code
        self.raw_headers = headers

    @property
    def headers(self):
        return dict(self.raw_headers)

    def __call__(self, environ, start_response):
        start_response(
            '{} {}'.format(self.status_code,
                           http_client.responses[self.status_code]),
            self.raw_headers
        )
        return self.content,


def is_optional_type(type_):
    # it have to be removed after nirum._compat.is_optional_type added.
    return is_union_type(type_) and type(None) in get_union_types(type_)


def match_request(rules, request_method, path_info, querystring):
    # Ignore root path.
    if path_info == '/':
        return None, None
    matched_verb = []
    match = None
    request_rules = sorted(rules, key=lambda x: x[1].names, reverse=True)
    for rule in request_rules:
        if isinstance(path_info, bytes):
            # FIXME Decode properly; URI is not unicode
            path_info = path_info.decode()
        variable_match = rule.matcher.match_path(path_info)
        querystring_match = True
        if querystring:
            querystring_match = rule.matcher.match_querystring(querystring)
            if querystring_match:
                variable_match.update(querystring_match)
        verb = rule.verb.upper()
        if variable_match and querystring_match:
            matched_verb.append(verb)
            if request_method in (rule.verb, 'OPTIONS') and \
                    match is None:
                match = PathMatch(match_group=variable_match, verb=verb,
                                  method_name=rule.name)
    return match, matched_verb


def parse_json_payload(request):
    if request.raw:
        try:
            json_payload = json.loads(request.text)
        except (TypeError, ValueError):
            raise InvalidJsonError(request.text)
        else:
            return json_payload
    else:
        return {}


class InvalidJsonError(ValueError):
    """Exception raised when a payload is not a valid JSON."""


class AnnotationError(ValueError):
    """Exception raised when the given Nirum annotation is invalid."""


class ServiceMethodError(LookupError):
    """Exception raised when a method is not found."""


class MethodDispatchError(ValueError):
    """Exception raised when failed to dispatch method."""

    def __init__(self, request, status_code, message=None,
                 *args, **kwargs):
        self.request = request
        self.status_code = status_code
        self.message = message
        super(MethodDispatchError, self).__init__(*args, **kwargs)


class MethodArgumentError(ValueError):
    """An exception type raised when the given method arguments are invalid."""

    def __init__(self):
        self.errors = set()
        super(MethodArgumentError, self).__init__('')

    def on_error(self, field, message):
        self.errors.add((field, message))
        line = '{0}: {1}'.format(field, message)
        if self.args[0]:
            line = '{0}\n{1}'.format(self.args[0], line)
        self.args = (line,)

    def raise_if_errored(self):
        if self.errors:
            raise self


class WsgiApp(object):
    """Create a WSGI application which adapts the given Nirum service.

    :param service: A service instance (not type) generated by Nirum compiler.
    :type service: :class:`nirum.service.Service`
    :param allowed_origins: A set of cross-domain origins allowed to access.
                            See also CORS_.
    :type allowed_origins: :class:`~typing.AbstractSet`\ [:class:`str`]
    :param allowed_headers: A set of allowed headers to request headers.
                            See also CORS_.
    :type allowed_headers: :class:`~typing.AbstractSet`\ [:class:`str`]

    .. _CORS: https://www.w3.org/TR/cors/

    """

    def __new__(cls, service, *args, **kwargs):
        if not isinstance(service, Service):
            if isinstance(service, type) and issubclass(service, Service):
                raise TypeError('expected an instance of {0.__module__}.'
                                '{0.__name__}, not uninstantiated service '
                                'class itself'.format(Service))
            raise TypeError(
                'expected an instance of {0.__module__}.{0.__name__}, not '
                '{1!r}'.format(Service, service)
            )
        if not issubclass(cls, LegacyWsgiApp) and \
           hasattr(type(service), '__nirum_schema_version__'):
            cls = LegacyWsgiApp
        return object.__new__(cls)

    def __init__(self, service,
                 allowed_origins=frozenset(),
                 allowed_headers=frozenset()):
        if not isinstance(service, Service):
            raise TypeError(
                'expected an instance of {0.__module__}.{0.__name__}, not '
                '{1!r}'.format(Service, service)
            )
        elif not isinstance(allowed_origins, collections.Set):
            raise TypeError('allowed_origins must be a set, not ' +
                            repr(allowed_origins))
        self.service = service
        self.allowed_origins = frozenset(d.strip().lower()
                                         for d in allowed_origins
                                         if '*' not in d)
        self.allowed_origin_patterns = frozenset(
            re.compile(
                '^' + '(?:[^.]+?)'.join(
                    map(re.escape, d.strip().lower().split('*'))
                ) + '$'
            )
            for d in allowed_origins
            if '*' in d
        )
        self.allowed_headers = frozenset(h.strip().lower()
                                         for h in allowed_headers)
        rules = []
        method_annoations = service.__nirum_method_annotations__
        service_methods = service.__nirum_service_methods__
        for method_name, annotations in method_annoations.items():
            try:
                params = annotations['http_resource']
            except KeyError:
                continue
            if not params['path'].lstrip('/'):
                raise AnnotationError(
                    'the root resource is reserved; '
                    'disallowed to route to the root'
                )
            try:
                uri_template = params['path']
                matcher = UriTemplateMatcher(uri_template)
                http_verb = params['method']
            except KeyError as e:
                raise AnnotationError('missing annotation parameter: ' +
                                      str(e))
            parameters = frozenset(
                service_methods[method_name]['_names'].values()
            )
            unsatisfied_parameters = parameters - matcher.names
            if unsatisfied_parameters:
                raise AnnotationError(
                    '"{0}" does not fully satisfy all parameters of {1}() '
                    'method; unsatisfied parameters are: {2}'.format(
                        uri_template, method_name,
                        ', '.join(sorted(unsatisfied_parameters))
                    )
                )
            rules.append(UriTemplateRule(
                uri_template=uri_template,
                matcher=matcher,
                verb=http_verb,
                name=method_name  # Service method
            ))
        rules.sort(key=lambda rule: rule.uri_template, reverse=True)
        self.rules = List(rules)

    def __call__(self, environ, start_response):
        """WSGI interface has to be callable."""
        return self.route(environ, start_response)

    def allows_origin(self, origin):
        parsed = urlparse.urlparse(origin)
        if parsed.scheme not in ('http', 'https'):
            return False
        host = parsed.hostname
        if host in self.allowed_origins:
            return True
        for pattern in self.allowed_origin_patterns:
            if pattern.match(host):
                return True
        return False

    def dispatch_method(self, environ):
        payload = None
        request = Request(environ)
        service_methods = self.service.__nirum_service_methods__
        # CORS
        cors_headers = [('Vary', 'Origin')]
        request_match, matched_verb = match_request(
            self.rules, request.method,
            request.path, request.querystring
        )
        if request_match:
            service_method = request_match.method_name
            cors_headers.append(
                (
                    'Access-Control-Allow-Methods',
                    ', '.join(matched_verb + ['OPTIONS'])
                )
            )
            method_parameters = {
                k: v
                for k, v in service_methods[request_match.method_name].items()
                if not k.startswith('_')
            }
            payload = {
                p.rstrip('_'): request_match.match_group.get_variable(p)
                for p in method_parameters
            }
            # TODO Parsing query string
            if request_match.verb not in ('GET', 'DELETE'):
                try:
                    json_payload = parse_json_payload(request)
                except InvalidJsonError as e:
                    raise MethodDispatchError(
                        request, 400,
                        "Invalid JSON payload: '{!s}'.".format(e)
                    )
                else:
                    payload.update(**json_payload)
        else:
            if request.method not in ('POST', 'OPTIONS'):
                raise MethodDispatchError(request, 405)
            cors_headers.append(
                ('Access-Control-Allow-Methods', 'POST, OPTIONS')
            )
            service_method = request.args.get('method')
            try:
                payload = parse_json_payload(request)
            except InvalidJsonError as e:
                raise MethodDispatchError(
                    request,
                    400,
                    "Invalid JSON payload: '{!s}'.".format(e)
                )
        if self.allowed_headers:
            cors_headers.append(
                (
                    'Access-Control-Allow-Headers',
                    ', '.join(sorted(self.allowed_headers))
                )
            )
        try:
            origin = request.headers['Origin']
        except KeyError:
            pass
        else:
            if self.allows_origin(origin):
                cors_headers.append(
                    ('Access-Control-Allow-Origin', origin)
                )
        return MethodDispatch(
            request=request,
            routed=bool(request_match),
            service_method=service_method,
            payload=payload,
            cors_headers=cors_headers
        )

    def route(self, environ, start_response):
        """Route an HTTP request to a corresponding service method,
        or respond with an error status code if it found nothing.

        :param environ: WSGI environment dictionary.
        :param start_response: A WSGI `start_response` callable.

        """
        try:
            match = self.dispatch_method(environ)
        except MethodDispatchError as e:
            response = self.error(e.status_code, e.request, e.message)
        else:
            if environ['REQUEST_METHOD'] == 'OPTIONS':
                start_response('200 OK', match.cors_headers)
                return []
            if match.service_method:
                try:
                    response = self.rpc(
                        match.request, match.service_method, match.payload
                    )
                except ServiceMethodError:
                    response = self.error(
                        404 if match.routed else 400,
                        match.request,
                        message='No service method `{}` found.'.format(
                            match.service_method
                        )
                    )
                else:
                    response.raw_headers += match.cors_headers
                    for k, v in match.cors_headers:
                        if k in response.headers:
                            # FIXME: is it proper?
                            response.headers[k] += ', ' + v
                        else:
                            response.headers[k] = v
            else:
                response = self.error(
                    400, match.request,
                    message="`method` is missing."
                )
        return response(environ, start_response)

    def rpc(self, request, service_method, request_json):
        name_map = self.service.__nirum_method_names__
        try:
            method_facial_name = name_map.behind_names[service_method]
        except KeyError:
            raise ServiceMethodError()
        try:
            func = getattr(self.service, method_facial_name)
        except AttributeError:
            return self.error(
                400,
                request,
                message="Service has no procedure '{}'.".format(service_method)
            )
        if not callable(func):
            return self.error(
                400, request,
                message="Remote procedure '{}' is not callable.".format(
                    service_method
                )
            )
        try:
            arguments = self._parse_procedure_arguments(
                method_facial_name,
                request_json
            )
        except MethodArgumentError as e:
            return self.error(
                400,
                request,
                message='There are invalid arguments.',
                errors=[
                    {'path': path, 'message': msg}
                    for path, msg in sorted(e.errors)
                ],
            )
        try:
            result = func(**arguments)
        except Exception as e:
            catched, resp = self._catch_exception(method_facial_name, e)
            if catched:
                return self._raw_response(400, resp)
            raise
        success, resp = self._respond_with_result(
            method_facial_name,
            result
        )
        if not success:
            service_class = type(self.service)
            logger = logging.getLogger(typing._type_repr(service_class)) \
                            .getChild(str(method_facial_name))
            if resp is None:
                logger.error(
                    '%s.%s() method must not return any value, but %r is '
                    'returned.',
                    typing._type_repr(service_class),
                    method_facial_name,
                    result
                )
            else:
                logger.error(
                    '%r is an invalid return value for the return type of '
                    '%s.%s() method.',
                    result,
                    typing._type_repr(service_class),
                    method_facial_name
                )
            hyphened_service_method = service_method.replace('_', '-')
            message = '''The server-side implementation of the {0}() method \
has tried to return a value of an invalid type.  \
It is an internal server error and should be fixed by server-side.'''.format(
                hyphened_service_method
            )
            if result is None:
                message = '''The return type of {0}() method is not optional \
(i.e., no trailing question mark), but its server-side implementation has \
tried to return nothing (i.e., null, nil, None).  It is an internal server \
error and should be fixed by server-side.'''.format(hyphened_service_method)
            return self.error(500, request, message=message)
        else:
            return self._raw_response(200, resp)

    def _parse_procedure_arguments(self, method_facial_name, request_json):
        for cls in type(self.service).__mro__:
            if not hasattr(cls, method_facial_name):
                continue
            method = getattr(cls, method_facial_name)
            if hasattr(method, '__nirum_deserialize_arguments__'):
                errors = MethodArgumentError()
                args = method.__nirum_deserialize_arguments__(
                    request_json,
                    errors.on_error
                )
                errors.raise_if_errored()
                return args
        assert False, \
            'could not find the method prototype; please report this bug'

    def _catch_exception(self, method_facial_name, exception):
        for cls in type(self.service).__mro__:
            if not hasattr(cls, method_facial_name):
                continue
            method = getattr(cls, method_facial_name)
            if hasattr(method, '__nirum_serialize_error__'):
                f = method.__nirum_serialize_error__
                if f is None:
                    return False, None
                try:
                    return True, f(exception)
                except TypeError:
                    return False, None
        assert False, \
            'could not find the method prototype; please report this bug'

    def _respond_with_result(self, method_facial_name, result):
        for cls in type(self.service).__mro__:
            if not hasattr(cls, method_facial_name):
                continue
            method = getattr(cls, method_facial_name)
            if hasattr(method, '__nirum_serialize_result__'):
                f = method.__nirum_serialize_result__
                if f is None:
                    return False, None
                try:
                    return True, f(result)
                except TypeError as e:
                    return False, e
        assert False, \
            'could not find the method prototype; please report this bug'

    def make_error_response(self, error_type, message=None, **kwargs):
        """Create error response json temporary.

        .. code-block:: nirum

           union error
               = not-found (text message)
               | bad-request (text message)
               | ...

        """
        # FIXME error response has to be generated from nirum core.
        e = {
            '_type': 'error',
            '_tag': error_type,
            'message': message,
        }
        e.update(**kwargs)
        return e

    def error(self, status_code, request, message=None, **kwargs):
        """Handle error response.

        :param int status_code:
        :param request:
        :return:

        """
        status_code_text = HTTP_STATUS_CODES.get(status_code, 'http error')
        status_error_tag = status_code_text.lower().replace(' ', '_')
        custom_response_map = {
            404: self.make_error_response(
                status_error_tag,
                'The requested URL {} was not found on this service.'.format(
                    request.path
                ),
                **kwargs
            ),
            400: self.make_error_response(status_error_tag, message, **kwargs),
            405: self.make_error_response(
                status_error_tag,
                'The requested URL {} was not allowed HTTP method {}.'.format(
                    request.path, request.method
                ),
                **kwargs
            ),
        }
        return self._raw_response(
            status_code,
            custom_response_map.get(
                status_code,
                self.make_error_response(
                    status_error_tag, message or status_code_text
                )
            )
        )

    def make_response(self, status_code, headers, content):
        return status_code, headers, content

    def _raw_response(self, status_code, response_json, **kwargs):
        response_tuple = self.make_response(
            status_code, headers=[('Content-type', 'application/json')],
            content=json.dumps(response_json).encode('utf-8')
        )
        if not (isinstance(response_tuple, collections.Sequence) and
                len(response_tuple) == 3):
            raise TypeError(
                'make_response() must return a triple of '
                '(status_code, headers, content), not ' + repr(response_tuple)
            )
        status_code, headers, content = response_tuple
        if not isinstance(status_code, integer_types):
            raise TypeError(
                '`status_code` have to be instance of integer. not {}'.format(
                    typing._type_repr(type(status_code))
                )
            )
        if not isinstance(headers, collections.Sequence):
            raise TypeError(
                '`headers` have to be instance of sequence. not {}'.format(
                    typing._type_repr(type(headers))
                )
            )
        if not isinstance(content, bytes):
            raise TypeError(
                '`content` have to be instance of bytes. not {}'.format(
                    typing._type_repr(type(content))
                )
            )
        return Response(content, status_code, headers, **kwargs)


class LegacyWsgiApp(WsgiApp):

    def __init__(self, service,
                 allowed_origins=frozenset(),
                 allowed_headers=frozenset()):
        super(LegacyWsgiApp, self).__init__(
            service=service,
            allowed_origins=allowed_origins,
            allowed_headers=allowed_headers
        )

    def _parse_procedure_arguments(self, method_facial_name, request_json):
        type_hints = self.service.__nirum_service_methods__[method_facial_name]
        arguments = {}
        version = type_hints.get('_v', 1)
        name_map = type_hints['_names']
        errors = MethodArgumentError()
        for argument_name, type_ in type_hints.items():
            if argument_name.startswith('_'):
                continue
            if version >= 2:
                type_ = type_()
            behind_name = name_map[argument_name]
            try:
                data = request_json[behind_name]
            except KeyError:
                if is_optional_type(type_):
                    arguments[argument_name] = None
                else:
                    errors.on_error('.' + behind_name, 'Expected to exist.')
                continue
            try:
                arguments[argument_name] = deserialize_meta(type_, data)
            except ValueError:
                errors.on_error(
                    '.' + behind_name,
                    'Expected {0}, but {1} was given.'.format(
                        typing._type_repr(type_),
                        typing._type_repr(type(data))
                    )
                )
        errors.raise_if_errored()
        return arguments

    def _catch_exception(self, method_facial_name, exception):
        method_error_types = self.service.__nirum_method_error_types__
        if not callable(method_error_types):
            # generated by the oldest compilers
            method_error_types = method_error_types.get
        method_error = method_error_types(method_facial_name, ())
        if isinstance(exception, method_error):
            return True, serialize_meta(exception)
        return False, None

    def _respond_with_result(self, method_facial_name, result):
        type_hints = self.service.__nirum_service_methods__[method_facial_name]
        return_type = type_hints['_return']
        if type_hints.get('_v', 1) >= 2:
            return_type = return_type()
        none_type = type(None)
        if return_type is none_type or is_optional_type(return_type):
            if result is None:
                return True, None
            return False, None
        if result is None:
            return False, TypeError('the return type cannot be None')
        try:
            serialized = serialize_meta(result)
            deserialize_meta(return_type, serialized)
        except ValueError as e:
            return False, e
        else:
            return True, serialized


class UriTemplateMatchResult(object):

    def __init__(self, result):
        if result is not None:
            self.result = List(result)
        else:
            self.result = None

    def __bool__(self):
        return self.result is not None

    __nonzero__ = __bool__

    def update(self, match_result):
        if self.result or match_result:
            self.result = List(
                itertools.chain(self.result or [], match_result.result or [])
            )

    def get_variable(self, variable_name):
        # Nirum compiler appends an underscore to the end of the given
        # `variable_name` if it's a reserved keyword by Python
        # (e.g. `from` → `from_`, `def` → `def_`).
        # So we need to remove a trailing underscore from the
        # `variable_name` (if it has one) before looking up match results.
        variable_name = variable_name.rstrip('_')
        values = [
            value
            for name, value in self.result
            if name == variable_name
        ]
        if values:
            return values if len(values) > 1 else values[0]
        else:
            return None


class UriTemplateMatcher(object):

    VARIABLE_PATTERN = re.compile(r'\{([a-zA-Z0-9_-]+)\}')

    def __init__(self, uri_template):
        if not isinstance(uri_template, text_type):
            raise TypeError('template must be a Unicode string, not ' +
                            repr(uri_template))
        if '?' in uri_template:
            path_template, querystring_template = uri_template.split('?')
        else:
            path_template = uri_template
            querystring_template = None
        self._names = []
        self.path_pattern = self.parse_path_template(path_template)
        self.querystring_pattern = self.parse_querystring_template(
            querystring_template
        )

    @property
    def names(self):
        return frozenset(self._names)

    def add_variable(self, name):
        if name in self.names:
            raise AnnotationError('every variable must not be duplicated: ' +
                                  name)
        self._names.append(name)

    def parse_path_template(self, template):
        result = []
        last_pos = 0
        for match in self.VARIABLE_PATTERN.finditer(template):
            variable = self.make_name(match.group(1))
            self.add_variable(variable)
            result.append(re.escape(template[last_pos:match.start()]))
            result.append(u'(?P<')
            result.append(variable)
            result.append(u'>.+?)')
            last_pos = match.end()
        result.append(re.escape(template[last_pos:]))
        result.append(u'$')
        return re.compile(u''.join(result))

    def parse_querystring_template(self, template):
        patterns = []
        if not template:
            return patterns
        qs_pattern = re.compile(
            '([\w-]+)={}'.format(self.VARIABLE_PATTERN.pattern)
        )
        for match in qs_pattern.finditer(template):
            variable = self.make_name(match.group(2))
            self.add_variable(variable)
            pattern = re.compile(
                '{0}=(?P<{1}>[^&]+)&?'.format(re.escape(match.group(1)),
                                              variable)
            )
            patterns.append(pattern)
        return patterns

    def make_name(self, name):
        return name.replace(u'-', u'_')

    def match_path(self, path):
        match = self.path_pattern.match(path)
        r = None
        if match:
            r = []
            if self.names:
                for name in self.names & set(match.groupdict().keys()):
                    r.append((name, match.group(name)))
        return UriTemplateMatchResult(r)

    def match_querystring(self, querystring):
        variables = []
        match_result = None
        for pattern in self.querystring_pattern:
            for match in pattern.finditer(querystring):
                for name in self.names & set(match.groupdict().keys()):
                    variables.append((name, match.group(name)))
        if len(set(n for n, _ in variables)) == len(self.querystring_pattern):
            match_result = UriTemplateMatchResult(variables)
        return match_result


IMPORT_RE = re.compile(
    r'''^
        (?P<modname> (?!\d) [\w]+
                     (?: \. (?!\d)[\w]+ )*
        )
        :
        (?P<clsexp> (?P<clsname> (?!\d) \w+ )
                    (?: \(.*\) )?
        )
    $''',
    re.X
)


def import_string(imp):
    m = IMPORT_RE.match(imp)
    if not m:
        raise ValueError(
            "malformed expression: {}, have to be x.y:z(...)".format(imp)
        )
    module_name = m.group('modname')
    import_root_mod = __import__(module_name)
    # it is used in `eval()`
    import_mod = reduce(getattr, module_name.split('.')[1:], import_root_mod)
    class_expression = m.group('clsexp')
    try:
        v = eval(class_expression, import_mod.__dict__, {})
    except AttributeError:
        raise ValueError("Can't import {}".format(imp))
    else:
        return v


def main():
    parser = argparse.ArgumentParser(description='Nirum service runner')
    parser.add_argument('-H', '--host', help='the host to listen',
                        default='0.0.0.0')
    parser.add_argument('-p', '--port', help='the port number to listen',
                        type=int, default=9322)
    parser.add_argument('-d', '--debug', help='debug mode',
                        action='store_true', default=False)
    parser.add_argument('service', help='Import path to service instance')
    args = parser.parse_args()
    if not ('.' in sys.path or os.getcwd() in sys.path):
        sys.path.insert(0, os.getcwd())
    service = import_string(args.service)
    run_simple(
        args.host, args.port, WsgiApp(service),
        use_reloader=args.debug, use_debugger=args.debug,
        use_evalex=args.debug
    )
