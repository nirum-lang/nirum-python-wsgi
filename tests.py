import collections
import json
import logging
import typing

from fixture import (BadRequest, CorsVerbService, MusicService,
                     NullDisallowedMethodService,
                     SatisfiedParametersService,
                     StatisticsService,
                     Unknown, UnsatisfiedParametersService)
from nirum.deserialize import deserialize_meta
from pytest import fixture, mark, raises
from six.moves import urllib
from werkzeug.test import Client
from werkzeug.wrappers import Response

from nirum_wsgi import (AnnotationError, LegacyWsgiApp, MethodArgumentError,
                        UriTemplateMatchResult, UriTemplateMatcher, WsgiApp,
                        import_string)


LEGACY = hasattr(MusicService, '__nirum_schema_version__')


class MusicServiceImpl(MusicService):

    music_map = {
        u'damien rice': [u'9 crimes', u'Elephant'],
        u'ed sheeran': [u'Thinking out loud', u'Photograph'],
        u'damien': [u'rice'],
    }

    def get_music_by_artist_name(self, artist_name):
        if artist_name == 'error':
            raise Unknown()
        elif artist_name not in self.music_map:
            raise BadRequest()
        return self.music_map.get(artist_name)

    def incorrect_return(self):
        return 1

    def get_artist_by_music(self, music):
        for k, v in self.music_map.items():
            if music in v:
                return k
        return u'none'

    def raise_application_error_request(self):
        raise ValueError('hello world')


class CorsVerbServiceImpl(CorsVerbService):

    def get_foo(self, foo):
        return True

    def update_foo(self, foo):
        return True

    def delete_bar(self, bar):
        return True


class StatisticsServiceImpl(StatisticsService):

    def purchase_count(self, from_, to):
        return list(range((to - from_).days))

    def purchase_interval(self, from_, to, interval):
        return list(range(int(interval)))

    def daily_purchase(self, exclude):
        if exclude is None:
            return [1]
        elif exclude:
            return [1, 2]
        else:
            return [1, 2, 3]


class NullDisallowedMethodServiceImpl(NullDisallowedMethodService):

    def __init__(self, value):
        self.value = value

    def null_disallowed_method(self):
        return self.value


@fixture
def fx_music_wsgi():
    return WsgiApp(MusicServiceImpl())


@fixture
def fx_test_client(fx_music_wsgi):
    return Client(fx_music_wsgi, Response)


def assert_response(response, status_code, expect_json):
    assert response.status_code == status_code, response.get_data(as_text=True)
    actual_response_json = json.loads(
        response.get_data(as_text=True)
    )
    assert actual_response_json == expect_json


def test_wsgi_app_error(caplog, fx_test_client):
    # method not allowed
    assert_response(
        fx_test_client.get('/?method=get_music_by_artist_name'), 405,
        {
            '_type': 'error',
            '_tag': 'method_not_allowed',
            'message': 'The requested URL / was not allowed HTTP method GET.'

        }
    )
    # method missing
    assert_response(
        fx_test_client.post('/'),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': u'`method` is missing.',

        }
    )
    # invalid procedure name
    assert_response(
        fx_test_client.post('/?method=foo'),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': 'No service method `foo` found.'

        }
    )
    # invalid json
    assert_response(
        fx_test_client.post(
            '/?method=get_music_by_artist_name', data="!",
            content_type='application/json'
        ),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': "Invalid JSON payload: '!'."

        }
    )
    # incorrect return
    caplog.handler.records = []  # Clear log records
    response = fx_test_client.post('/?method=incorrect_return')
    assert caplog.record_tuples and caplog.record_tuples[-1] == (
        typing._type_repr(MusicServiceImpl) + '.incorrect_return',
        logging.ERROR,
        '''1 is an invalid return value for the return type of {0}.\
incorrect_return() method.'''.format(
            typing._type_repr(MusicServiceImpl)
        ),
    )
    assert_response(
        response,
        500,
        {
            '_type': 'error',
            '_tag': 'internal_server_error',
            'message': '''The server-side implementation of the \
incorrect-return() method has tried to return a value of an invalid type.  \
It is an internal server error and should be fixed by server-side.''',
        }
    )


def test_procedure_bad_request(fx_test_client):
    assert_response(
        fx_test_client.post('/?method=get_music_by_artist_name'),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': 'There are invalid arguments.',
            'errors': [
                {'path': '.artist_name', 'message': 'Expected to exist.'},
            ],
        }
    )
    payload = {
        'artist_name': 1
    }
    assert_response(
        fx_test_client.post(
            '/?method=get_music_by_artist_name',
            data=json.dumps(payload),
            content_type='application/json'
        ),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': 'There are invalid arguments.',
            'errors': [
                {
                    'path': '.artist_name',
                    'message': (
                        'Expected {0}, but int was given.'.format(
                            type(u'').__name__
                        )
                        if LEGACY
                        else 'Expected a string.'
                    )
                },
            ],
        }
    )


@mark.parametrize(
    'payload, expected_json',
    [
        ({'artist_name': u'damien rice'}, [u'9 crimes', u'Elephant']),
        (
            {'artist_name': u'ed sheeran'},
            [u'Thinking out loud', u'Photograph']
        ),
    ]
)
def test_wsgi_app_method(fx_test_client, payload, expected_json):
    response = fx_test_client.post(
        '/?method=get_music_by_artist_name',
        data=json.dumps(payload),
        content_type='application/json'
    )
    data = json.loads(response.get_data(as_text=True))
    assert data == expected_json


def test_wsgi_app_http_error(fx_test_client):
    response = fx_test_client.post('/foobar')  # 404
    assert response.status_code == 400
    response_json = json.loads(response.get_data(as_text=True))
    assert response_json == {
        '_type': 'error',
        '_tag': u'bad_request',
        'message': u'`method` is missing.',
    }


def test_wsgi_app_with_behind_name(fx_test_client):
    payload = {'norae': u'9 crimes'}
    assert_response(
        fx_test_client.post(
            '/?method=get_artist_by_music',
            data=json.dumps(payload),
            content_type='application/json'
        ),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': 'No service method `get_artist_by_music` found.'

        }
    )
    assert_response(
        fx_test_client.post(
            '/?method=find_artist',
            data=json.dumps({'music': '9 crimes'}),
            content_type='application/json'
        ),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': 'There are invalid arguments.',
            'errors': [
                {'path': '.norae', 'message': 'Expected to exist.'},
            ],
        }
    )
    assert_response(
        fx_test_client.post(
            '/?method=find_artist',
            data=json.dumps(payload),
            content_type='application/json'
        ),
        200,
        u'damien rice'
    )


@mark.parametrize('arity', [0, 1, 2, 4])
def test_wsgi_app_make_response_arity_check(arity):
    class ExtendedWsgiApp(LegacyWsgiApp if LEGACY else WsgiApp):
        def make_response(self, status_code, headers, content):
            return (status_code, headers, content, None)[:arity]
    wsgi_app = ExtendedWsgiApp(MusicServiceImpl())
    client = Client(wsgi_app, Response)
    with raises(TypeError) as e:
        client.post('/?method=get_music_by_artist_name',
                    data=json.dumps({'artist_name': u'damien rice'}))
    assert str(e.value).startswith('make_response() must return a triple of '
                                   '(status_code, headers, content), not ')


@mark.parametrize('uri_template, pattern, variables, valid, invalid', [
    (
        u'/foo/{id}/bar.txt',
        r'\/foo\/(?P<id>.+?)\/bar\.txt$',
        {'id'},
        ['/foo/xyz/bar.txt', '/foo/123/bar.txt'],
        ['/bar/xyz/bar.txt', '/foo/bar.txt'],
    ),
    (
        u'/foo/{id}',
        r'\/foo\/(?P<id>.+?)$',
        {'id'},
        ['/foo/xyz'],
        ['/bar/xyz/bar.txt'],
    ),
    (
        u'/foo/{foo-id}',
        r'\/foo\/(?P<foo_id>.+?)$',
        {'foo_id'},
        ['/foo/xyz'],
        ['/bar/xyz/bar.txt'],
    ),
    (
        u'/foo/{id}/bar/{id2}',
        r'\/foo\/(?P<id>.+?)\/bar\/(?P<id2>.+?)$',
        {'id', 'id2'},
        ['/foo/xyz/bar/123', '/foo/123/bar/abc'],
        ['/bar/xyz/bar.txt', '/bar/bar.txt'],
    ),
    (
        u'/foo/bar',
        r'\/foo\/bar$',
        set(),
        ['/foo/bar'],
        ['/lorem/ipsum', '/prefix/foo/bar', '/foo/bar/postfix'],
    ),
])
def test_uri_template_matcher(uri_template, pattern, variables, valid,
                              invalid):
    matcher = UriTemplateMatcher(uri_template)
    assert matcher.names == variables
    assert matcher.path_pattern.pattern == pattern
    for v in valid:
        assert matcher.path_pattern.match(v), v
        assert matcher.match_path(v), v
    for v in invalid:
        assert not matcher.path_pattern.match(v), v
        assert not matcher.match_path(v), v


@mark.parametrize('uri_template, variables, valid, invalid', [
    (
        u'/foo/?from={from}&to={to}',
        {'from', 'to'},
        ['/foo/?from=1&to=2', '/foo/?to=2&from=1'],
        ['/foo/?from=1', '/foo/?to=2'],
    ),
    (
        u'/foo/?start-from={start-from}&end-to={end-to}',
        {'start_from', 'end_to'},
        ['/foo?start-from=1&end-to=2', '/foo?end-to=2&start-from=1'],
        ['/foo?start-from=1', '/foo?end-to=2'],
    ),
])
def test_uri_template_matcher_querystring(
    uri_template, variables, valid, invalid
):
    matcher = UriTemplateMatcher(uri_template)
    assert matcher.names == variables
    for v in valid:
        assert matcher.match_querystring(v), v
    for v in invalid:
        assert not matcher.match_querystring(v), v


def test_uri_template_matcher_duplicate_variable_error():
    with raises(AnnotationError):
        UriTemplateMatcher(u'/foo/{var}/bar/{var}')


@mark.parametrize('lval, rval, expected', [
    (None, [('n1', 'v1'), ('n2', 'v2')], [('n1', 'v1'), ('n2', 'v2')]),
    ([('n1', 'v1'), ('n2', 'v2')], None, [('n1', 'v1'), ('n2', 'v2')]),
    ([('n1', 'v1'), ('n2', 'v2')], [('n1', 'v3'), ('n2', 'v4')],
     [('n1', 'v1'), ('n2', 'v2'), ('n1', 'v3'), ('n2', 'v4')]),
    (None, None, None),
])
def test_uri_template_match_result_update(lval, rval, expected):
    lval_result = UriTemplateMatchResult(lval)
    rval_result = UriTemplateMatchResult(rval)
    lval_result.update(rval_result)
    if expected is None:
        assert lval is None
    else:
        assert list(lval_result.result) == expected


def test_import_string():
    assert import_string('collections:OrderedDict') == collections.OrderedDict
    assert (import_string('collections:OrderedDict({"a": 1})') ==
            collections.OrderedDict({"a": 1}))
    with raises(ValueError):
        # malformed
        import_string('world')
    with raises(NameError):
        # coudn't import
        import_string('os:world')
    with raises(ImportError):
        # coudn't import
        import_string('os.hello:world')


def test_unsatisfied_uri_template_parameters():
    s = UnsatisfiedParametersService()
    with raises(AnnotationError) as e:
        WsgiApp(s)
    assert str(e.value) == (
        '"/foo/{bar}/" does not fully satisfy all parameters of foo_bar_baz() '
        'method; unsatisfied parameters are: baz, foo'
    )
    # As parameter names overlapped to Python keywords append an underscore
    # to their names, it should deal with the case as well.
    s = SatisfiedParametersService()
    WsgiApp(s)  # Should not raise AnnotationError


def test_http_resource_route(fx_test_client):
    assert_response(
        fx_test_client.get('/artists/damien/'),
        200,
        [u'rice'],
    )


def split(header, lower=False):
    vs = [h.strip() for h in header.split(',')]
    if lower:
        vs = [v.lower() for v in vs]
    return frozenset(vs)


def test_cors():
    app = WsgiApp(
        MusicServiceImpl(),
        allowed_origins=frozenset(['example.com'])
    )
    client = Client(app, Response)
    resp = client.options('/?method=get_music_by_artist_name', headers={
        'Origin': 'https://example.com',
        'Access-Control-Request-Method': 'POST',
    })
    assert resp.status_code == 200
    assert resp.headers['Access-Control-Allow-Origin'] == 'https://example.com'
    assert split(resp.headers['Access-Control-Allow-Methods']) == {
        'POST', 'OPTIONS',
    }
    assert 'origin' in split(resp.headers['Vary'], lower=True)

    resp2 = client.post(
        '/?method=get_music_by_artist_name',
        headers={
            'Origin': 'https://example.com',
            'Access-Control-Request-Method': 'POST',
            'Content-Type': 'application/json',
        },
        data=json.dumps({'artist_name': 'damien'})
    )
    assert resp2.status_code == 200, resp2.get_data(as_text=True)
    assert resp2.headers['Access-Control-Allow-Origin'] == \
        'https://example.com'
    assert {'POST', 'OPTIONS'} == split(
        resp2.headers['Access-Control-Allow-Methods']
    )
    assert 'origin' in split(resp2.headers['Vary'], lower=True)

    resp3 = client.options('/?method=get_music_by_artist_name', headers={
        'Origin': 'https://disallowed.com',
        'Access-Control-Request-Method': 'POST',
    })
    assert resp3.status_code == 200
    allow_origin = resp3.headers.get('Access-Control-Allow-Origin', '')
    assert 'disallowed.com' not in allow_origin


@mark.parametrize('origin, disallowed_origin_host', [
    (u'https://example.com', u'disallowed.com'),
    (u'https://foobar.prefix.example.com', u'foobar.nonprefix.example.com'),
    (u'https://foobar.prefix.example.com', u'prefix.example.com'),
    (u'https://foobar.prefix.example.com', u'foobarprefix.example.com'),
    (u'https://infix.foobar.example.com', u'disallowed.foobar.example.com'),
])
@mark.parametrize(
    'url, allow_methods, request_method',
    [
        (u'/foo/abc/', {u'GET', u'PUT', u'OPTIONS'}, u'GET'),
        (u'/foo/abc/', {u'GET', u'PUT', u'OPTIONS'}, u'PUT'),
        (u'/bar/abc/', {u'DELETE', u'OPTIONS'}, u'DELETE'),
    ],
)
def test_cors_http_resouce(origin, disallowed_origin_host,
                           url, allow_methods, request_method):
    app = WsgiApp(
        CorsVerbServiceImpl(),
        allowed_origins=frozenset([
            'example.com',
            '*.prefix.example.com',
            'infix.*.example.com',
        ])
    )
    assert app.allows_origin(origin)
    assert not app.allows_origin(u'http://' + disallowed_origin_host)
    assert not app.allows_origin(u'https://' + disallowed_origin_host)

    client = Client(app, Response)
    resp = client.options(url, headers={
        'Origin': origin,
        'Access-Control-Request-Method': request_method,
    })
    assert resp.status_code == 200
    assert resp.headers['Access-Control-Allow-Origin'] == origin
    assert split(resp.headers['Access-Control-Allow-Methods']) == allow_methods
    assert u'origin' in split(resp.headers['Vary'], lower=True)
    resp2 = getattr(client, request_method.lower())(
        url,
        headers={
            'Origin': origin,
            'Access-Control-Request-Method': request_method,
            'Content-Type': u'application/json',
        },
    )
    assert resp2.status_code == 200, resp2.get_data(as_text=True)
    assert resp2.headers['Access-Control-Allow-Origin'] == origin
    assert allow_methods == split(
        resp2.headers['Access-Control-Allow-Methods']
    )
    assert 'origin' in split(resp2.headers['Vary'], lower=True)

    resp3 = client.options(url, headers={
        'Origin': u'https://' + disallowed_origin_host,
        'Access-Control-Request-Method': request_method,
    })
    assert resp3.status_code == 200
    allow_origin = resp3.headers.get('Access-Control-Allow-Origin', u'')
    assert disallowed_origin_host not in allow_origin


@mark.parametrize('qs, expected', [
    ([('from', '2017-01-01'), ('to', '2017-01-30')], list(range(29))),
    ([('to', '2017-01-30'), ('from', '2017-01-01')], list(range(29))),
    # Ignore unused argument.
    (
        [('to', '2017-01-30'), ('from', '2017-01-01'), ('x', 1)],
        list(range(29))
    ),
    # Match with `purchase-interval`
    (
        [('to', '2017-01-30'), ('from', '2017-01-01'), ('interval', 10)],
        list(range(10))
    ),
    # Match with `purchase-interval` ignore unused argument.
    (
        [
            ('to', '2017-01-30'),
            ('from', '2017-01-01'),
            ('interval', 10), ('x', 1)
        ],
        list(range(10))
    ),
])
def test_resolve_querystring(qs, expected):
    app = WsgiApp(
        StatisticsServiceImpl(),
        allowed_origins=frozenset(['example.com'])
    )
    client = Client(app, Response)
    url = '/statistics/purchases/?' + urllib.parse.urlencode(qs)
    response = client.get(url)
    assert response.status_code == 200, response.get_data(as_text=True)
    return_result = deserialize_meta(
        typing.Sequence[int], json.loads(response.get_data(as_text=True))
    )
    assert return_result == expected


@mark.parametrize('payload, expected', [
    ({'exclude': False}, [1, 2, 3]),
    ({'exclude': True}, [1, 2]),
    ({'exclude': None}, [1]),
    ({}, [1]),
])
def test_omit_optional_parameter(payload, expected):
    app = WsgiApp(StatisticsServiceImpl())
    client = Client(app, Response)
    response = client.post(
        '/?method=daily_purchase',
        data=json.dumps(payload),
        content_type='application/json'
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    actual = json.loads(response.get_data(as_text=True))
    assert actual == expected


def test_readable_error_when_null_returned_from_null_disallowed_method(caplog):
    """Even if the method implementation returns None (FYI Python functions
    return None when it lacks return statement so that service methods are
    prone to return None by mistake) the error message should be readable
    and helpful for debugging.

    """
    expected_message = '''The return type of null-disallowed-method() method \
is not optional (i.e., no trailing question mark), but its server-side \
implementation has tried to return nothing (i.e., null, nil, None).  \
It is an internal server error and should be fixed by server-side.'''
    app = WsgiApp(NullDisallowedMethodServiceImpl(None))
    client = Client(app, Response)
    caplog.handler.records = []  # Clear log records
    response = client.post(
        '/?method=null_disallowed_method',
        data=json.dumps({}),
        content_type='application/json'
    )
    assert caplog.record_tuples and caplog.record_tuples[-1] == (
        '{0}.null_disallowed_method'.format(
            typing._type_repr(NullDisallowedMethodServiceImpl)
        ),
        logging.ERROR,
        '''None is an invalid return value for the return type of {0}.\
null_disallowed_method() method.'''.format(
            typing._type_repr(NullDisallowedMethodServiceImpl)
        ),
    )
    assert response.status_code == 500, response.get_data(as_text=True)
    actual = json.loads(response.get_data(as_text=True))
    assert actual == {
        '_type': 'error',
        '_tag': 'internal_server_error',
        'message': expected_message,
    }


def test_method_argument_error():
    e = MethodArgumentError()
    assert not e.errors
    assert str(e) == ''
    e.on_error('.foo', 'Message A.')
    assert e.errors == {('.foo', 'Message A.')}
    assert str(e) == '.foo: Message A.'
    e.on_error('.bar', 'Message B.')
    assert e.errors == {('.foo', 'Message A.'), ('.bar', 'Message B.')}
    assert str(e) == '.foo: Message A.\n.bar: Message B.'
