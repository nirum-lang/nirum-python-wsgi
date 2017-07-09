import collections
import json

from fixture import BadRequest, MusicService, Unknown
from pytest import fixture, raises, mark
from six import text_type
from werkzeug.test import Client
from werkzeug.wrappers import Response

from nirum_wsgi import WsgiApp, import_string


class MusicServiceImpl(MusicService):

    music_map = {
        u'damien rice': [u'9 crimes', u'Elephant'],
        u'ed sheeran': [u'Thinking out loud', u'Photograph'],
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


@fixture
def fx_music_wsgi():
    return WsgiApp(MusicServiceImpl())


@fixture
def fx_test_client(fx_music_wsgi):
    return Client(fx_music_wsgi, Response)


def test_wsgi_app_ping(fx_music_wsgi, fx_test_client):
    assert fx_music_wsgi.service
    response = fx_test_client.get('/ping/')
    data = json.loads(response.get_data(as_text=True))
    assert 'Ok' == data


def assert_response(response, status_code, expect_json):
    assert response.status_code == status_code, response.get_data(as_text=True)
    actual_response_json = json.loads(
        response.get_data(as_text=True)
    )
    assert actual_response_json == expect_json


def test_wsgi_app_error(fx_test_client):
    # method not allowed
    assert_response(
        fx_test_client.get('/?method=get_music_by_artist_name'),
        405,
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
            'message': "A query string parameter method= is missing."

        }
    )
    # invalid procedure name
    assert_response(
        fx_test_client.post('/?method=foo'),
        400,
        {
            '_type': 'error',
            '_tag': 'bad_request',
            'message': "Service dosen't have procedure named 'foo'."

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
    response = fx_test_client.post('/foobar')
    assert response.status_code == 404
    response_json = json.loads(response.get_data(as_text=True))
    assert response_json == {
        '_type': 'error',
        '_tag': 'not_found',
        'message': 'The requested URL /foobar was not found on this service.',
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
            'message': "Service dosen't have procedure named "
                       "'get_artist_by_music'."

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
