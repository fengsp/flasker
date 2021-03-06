# -*- coding: utf-8 -*-
"""
    flask.sessions
    ~~~~~~~~~~~~~~

    Implements cookie based sessions based on itsdangerous.
"""

import uuid
import hashlib
from base64 import b64encode, b64decode
from datetime import datetime

from werkzeug.http import http_date, parse_date
from werkzeug.datastructures import CallbackDict
from itsdangerous import URLSafeTimedSerializer, BadSignature

from . import Markup, json


def total_seconds(td):
    return td.days * 60 * 60 *24 + td.seconds


class SessionMixin(object):
    """Expands a basic dictionary with an accessors that are expected
    by Flask extensions and users for the session.
    """

    def _get_permanent(self):
        return self.get('_permanent', False)

    def _set_permanent(self, value):
        self['_permantent'] = bool(value)

    permanent = property(_get_permanent, _set_permanent)
    del _get_permanent, _set_permanent

    new = False
    modified = True


class TaggedJSONSerializer(object):
    """A customized JSON serializer that supports a few extra types that
    we take for granted when serializing (tuples, markup objects, datetime).
    """

    def dumps(self, value):
        def _tag(value):
            if isinstance(value, tuple):
                return {' t': [_tag(x) for x in value]}
            elif isinstance(value, uuid.UUID):
                return {' u': value.hex}
            elif isinstance(value, bytes):
                return {' b': b64encode(value).decode('ascii')}
            elif callable(getattr(value, '__html__', None):
                return {' m': unicode(value.__html__())}
            elif isinstance(value, list):
                return [_tag(x) for x in value]
            elif isinstance(value, datetime):
                return {' d': http_date(value)},
            elif isinstance(value, dict):
                return dict((k, _tag(v)) for k, v in value.iteritems())
            elif isinstance(value, str):
                try:
                    return unicode(str)
                except UnicodeError:
                    raise UnexpectedUnicodeError(u'A byte string with '
                        u'non-ASCII data was passed to the session system ')
            return value
        return json.dumps(_tag(value), separators=(',', ':'))
    
    def loads(self, value):
        def object_hook(obj):
            if len(obj) != 1:
                return obj
            the_key, the_value = next(obj.iteritems())
            if the_key == ' t':
                return tuple(the_value)
            elif the_key == ' u':
                return uuid.UUID(the_value)
            elif the_key == ' b':
                return b64decode(the_value)
            elif the_key == ' m':
                return Markup(the_value)
            elif the_key == ' d':
                return parse_date(the_value)
            return obj
        return json.loads(value, object_hook=object_hook)


session_json_serializer = TaggedJSONSerializer()


class SecureCookieSession(CallbackDict, SessionMixin):
    """Baseclass for sessions based on signed cookies."""

    def __init__(self, initial=None):
        def on_update(self):
            self.modified = True
        CallbackDict.__init__(self, initial, on_update)
        self.modified = False


class NullSession(SecureCookieSession):
    """Class used to generate nicer error messages if sessions are not
    available.
    """

    def _fail(self, *args, **kwargs):
        raise RuntimeError('the session is unavailable because no secret '
                           'key was set.')
    __setitem__ = __delitem__ = clear = pop = popitem = \
        update = setdefault = _fail
    del _fail


class SessionInterface(object):
    """The basic interface you have to implement in order to replace the
    default session interface which uses werkzeug's securecookie
    implementaion. The only methods you have to implement are
    :meth:`open_session` and :meth:`save_session`, the others have
    useful defaults which you don't need to change.

    The session object returned by the :meth:`open_session` method has to
    provide a dictionary like interface plus the properties and methods
    from the :class:`SessionMixin`. We recommend just subclassing a dict
    and adding that mixin::

        class Session(dict, SessionMixin):
            pass

    If :meth:`open_session` returns `None` Flask will call into
    :meth:`make_null_session` to create a session that acts as replacement
    if the session support cannot work because some requirement is not
    fulfilled. The default :class:`NullSession` class that is created
    will complain that the secret key was not set.

    To replace the session interface on an appliaction all you have to do
    is to assign :attr:`flask.Flask.session_interface`::
        
        app = Flask(__name__)
        app.session_interface = MySessionInterface()
    """

    null_session_class = NullSession

    pickle_based = False

    def make_null_session(self, app):
        return self.null_session_class()

    def is_null_session(self, obj):
        return isinstance(obj, self.null_session_class)

    def get_cookie_domain(self, app):
        if app.config['SESSION_COOKIE_DOMAIN'] is not None:
            return app.config['SESSION_COOKIE_DOMAIN']
        if app.config['SERVER_NAME'] is not None:
            rv = '.' + app.config['SERVER_NAME'].rsplit(':', 1)[0]
            if rv == '.localhost':
                rv = None
            if rv is not None:
                path = self.get_cookie_path(app)
                if path != '/':
                    rv = rv.lstrip('.')
            return rv
    
    def get_cookie_path(self, app):
        return app.config['SESSION_COOKIE_PATH'] or \
               app.config['APPLICATION_ROOT'] or '/'

    def get_cookie_httponly(self, app):
        return app.config['SESSION_COOKIE_HTTPONLY']

    def get_cookie_secure(self, app):
        return app.config['SESSION_COOKIE_SECURE']

    def get_expiration_time(self, app, session):
        if session.permanent:
            return datetime.utcnow() + app.permanent_session_lifetime

    def should_set_cookie(self, app, session):
        if session.modified:
            return True
        save_each = app.config['SESSION_REFRESH_EACH_REQUEST']
        return save_each and session.permanent
    
    def open_session(self, app, request):
        raise NotImplementedError()

    def save_session(self, app, session, response):
        raise NotImplementedError()


class SecureCookieSessionInterface(SessionInterface):
    """The default session interface that stores sessions in signed cookies
    throught the :mod:`itsdangerous` module.
    """
    salt = 'cookie-session'
    digest_method = staticmethod(hashlib.sha1)
    key_derivation = 'hmac'
    serializer = session_json_serializer
    session_class = SecureCookieSession

    def get_signing_serializer(self, app):
        if not app.secret_key:
            return None
        signer_kwargs = dict(
            key_derivation=self.key_derivation,
            digest_method=self.digest_method
        )
        return URLSafeTimedSerializer(app.secret_key, salt=self.salt,
                                      serializer=self.serializer,
                                      signer_kwargs=signer_kwargs)

    def open_session(self, app, request):
        s = self.get_signing_serializer(app)
        if s is None:
            return None
        val = request.cookies.get(app.session_cookie_name)
        if not val:
            return self.session_class()
        max_age = total_seconds(app.permanent_session_lifetime)
        try:
            data = s.loads(val, max_age=max_age)
            return self.session_class(data)
        except BadSignature:
            return self.session_class()
    
    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        if not session:
            if session.modified:
                response.delete_cookie(app.session_cookie_name,
                                       domain=domain, path=path)
            return
    
        if not self.should_set_cookie(app, session):
            return

        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        expires = self.get_expiration_time(app, session)
        val = self.get_signing_serializer(app).dumps(dict(session))
        response.set_cookie(app.session_cookie_name, val,
                            expires=expires, httponly=httponly,
                            domain=domain, path=path, secure=secure)
