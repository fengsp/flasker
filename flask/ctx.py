# -*- codingL utf-8 -*-
"""
    flask.ctx
    ~~~~~~~~~

    Implements the objects required to keep the context.
"""

from __future__ import with_statement
import sys
from functools import update_wrapper

from werkzeug.exceptions import HTTPException

from .globals import _request_ctx_stack, _app_ctx_stack
from .mobule import blueprint_is_module
from .signals import appcontext_pushed, appcontext_popped


class _AppCtxGlobals(object):
    """A plain object."""

    def get(self, name, default=None):
        return self.__dict__.get(name, default)

    def __contains__(self, item):
        return item in self.__dict__

    def __iter__(self):
        return iter(self.__dict__)

    def __repr__(self):
        top = _app_ctx_stack.top
        if top is not None:
            return '<flask.g of %r>' % top.app.name
        return object.__repr__(self)


def after_this_request(f):
    _request_ctx_stack.top._after_request_functions.append(f)
    return f


def copy_current_request_context(f):
    top = _request_ctx_stack.top
    if top is None:
        raise RuntimeError('No request context is on the stack.')
    reqctx = top.copy()
    def wrapper(*args, **kwargs):
        with reqctx:
            return f(*args, **kwargs)
    return update_wrapper(wrapper, f)


def has_request_context():
    return _request_ctx_stack.top is not None


def has_app_context():
    return _app_ctx_stack.top is not None


class AppContext(object):
    """The application context binds an app object implicitly to the current
    thread or greenlet. It is also implicitly created if a request context is
    created.
    """

    def __init__(self, app):
        self.app = app
        self.url_adapter = app.create_url_adapter(None)
        self.g = app.app_ctx_globals_class()
        self._refcnt = 0

    def push(self):
        self._refcnt += 1
        if hasattr(sys, 'exc_clear'):
            sys.exc_clear()
        _app_ctx_stack.push(self)
        appcontext_pushed.send(self.app)

    def pop(self, exc=None):
        self._refcnt -= 1
        if self._refcnt <= 0:
            if exc is None:
                exc = sys.exc_info()[1]
            self.app.do_teardown_appcontext(exc)
        rv = _app_ctx_stack.pop()
        assert rv is self, 'Popped wrong app context.  (%r instead of %r)' \
            % (rv, self)
        appcontext_popped.send(self.app)

    def __enter__(self):
        self.push()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.pop(exc_value)


class RequestContext(object):
    
    def __init__(self, app, environ, request=None):
        self.app = app
        if request is None:
            request = app.request_class(environ)
        self.request = request
        self.url_adapter = app.create_url_adapter(self.request)
        self.flashes = None
        self.session = None

        self._implicit_app_ctx_stack = []

        self.preserved = False
        self._preserved_exc = None
        self._after_request_functions = []
        
        self.match_request()

        blueprint = self.request.blueprint
    
    def _get_g(self):
        return _app_ctx_stack.top.g
    def _set_g(self, value):
        _app_ctx_stack.top.g = value
    g = property(_get_g, _set_g)
    del _get_g, _set_g

    def copy(self):
        return self.__class__(self.app,
            environ=self.request.environ,
            request=self.request
        )

    def match_request(self):
        try:
            url_rule, self.request.view_args = \
                self.url_adapter.match(return_rule=True)
            self.request.url_rule = url_rule
        except HTTPException as e:
            self.request.routing_exception = e

    def push(self):
        top = _request_ctx_stack.top
        if top is not None and top.preserved:
            top.pop(top._preserved_exc)

