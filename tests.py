import collections
import json

from fixture import (BadRequest, CorsVerbService, MusicService,
                     SatisfiedParametersService,
                     Unknown, UnsatisfiedParametersService)
from pytest import fixture, mark, raises
from six import text_type
from werkzeug.test import Client
from werkzeug.wrappers import Response

from nirum_wsgi import (AnnotationError, WsgiApp,
                        compile_uri_template, import_string)


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


def test_wsgi_app_error(fx_test_client):
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
    assert_response(
        fx_test_client.post('/?method=incorrect_return'),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': "Incorrect return type 'int' for 'incorrect_return'. "
                       "expected '{}'.".format(text_type.__name__)
        }
    )


def test_procedure_bad_request(fx_test_client):
    assert_response(
        fx_test_client.post('/?method=get_music_by_artist_name'),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': "A argument named 'artist_name' is missing, "
                       "it is required.",
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
            'message': "Incorrect type 'int' for 'artist_name'. "
                       "expected '{}'.".format(text_type.__name__)
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
            'message': "A argument named 'norae' is missing, "
                       "it is required.",
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
    class ExtendedWsgiApp(WsgiApp):
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
def test_compile_uri_template(uri_template, pattern, variables, valid,
                              invalid):
    p, compiled_variables = compile_uri_template(uri_template)
    assert compiled_variables == variables
    assert p.pattern == pattern
    for v in valid:
        assert p.match(v), v
    for v in invalid:
        assert not p.match(v), v


def test_compile_uri_template_duplicate_variable_error():
    with raises(AnnotationError):
        compile_uri_template(u'/foo/{var}/bar/{var}')


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


@mark.parametrize(
    'url, allow_methods, request_method',
    [
        (u'/foo/abc/', {u'GET', u'PUT', u'OPTIONS'}, u'GET'),
        (u'/foo/abc/', {u'GET', u'PUT', u'OPTIONS'}, u'PUT'),
        (u'/bar/abc/', {u'DELETE', u'OPTIONS'}, u'DELETE'),
    ],
)
def test_cors_http_resouce(url, allow_methods, request_method):
    app = WsgiApp(
        CorsVerbServiceImpl(),
        allowed_origins=frozenset(['example.com'])
    )
    client = Client(app, Response)
    origin = u'https://example.com'
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
            'Origin': u'https://example.com',
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
        'Origin': u'https://disallowed.com',
        'Access-Control-Request-Method': request_method,
    })
    assert resp3.status_code == 200
    allow_origin = resp3.headers.get('Access-Control-Allow-Origin', u'')
    assert u'disallowed.com' not in allow_origin
