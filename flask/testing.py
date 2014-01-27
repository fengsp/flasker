# -*- coding: utf-8 -*-
"""
    flask.testing
    ~~~~~~~~~~~~~

    Implements test support helpers. This module is lazily imported
    and ususlly not used in production environments.
"""

from contextlib import contextmanager

from werkzeug.test import Client, EnvironBuilder

from .ctx import _request_ctx_stack

try:
    from werkzeug.urls import url_parse
except ImportError:
    from urlparse import urlsplit as url_parse


def make_test_environ_builder(app, path='/', base_url=None, *args, **kwargs):
    """Creates a new test builder with some application defaults thrown in."""
    http_host = app.config.get('SERVER_NAME')
    app_root = app.config.get('APPLICATION_ROOT')
    if base_url is None:
        url = url_parse(path)
        base_url = 'http://%s/' % (url.netloc or http_host or 'localhost')
        if app_root:
            base_url += app_root.lstrip('/')
        if url.netloc:
            path = url.path
    return EnvironBuilder(path, base_url, *args, **kwargs)


class FlaskClient(Client):
    """Works like a regular Werkzeug test client but has some knowledge about
    how Flask words to defer the cleanup of the request context stack to the 
    end of a with body when used in a with statement.  For general information
    about how to use the class refer to :class:`werkzeug.test.Client`.

    Basic usage is outlined in the :ref:`testing` chapter.
    """

    preserve_context = False

    @contextmanager
    def session_transaction(self, *args, **kwargs):
        if self.cookie_jar is None:
            raise RuntimeError('No Session without cookies enabled.')

        app = self.application
        environ_overrides = kwargs.setdefault('environ_overrides', {})
        self.cookie_jar.inject_wsgi(environ_overrides)
        outer_reqctx = _request_ctx_stack.top
        with app.test_request_context(*args, **kwargs) as c:
            sess = app.open_session(c.request)
            if sess is None:
                raise RuntimeError('Session not opened.')
            _request_ctx_stack.push(outer_reqctx)
            try:
                yield sess
            finally:
                _request_ctx_stack.pop()
            
            resp = app.response_class()
            if not app.session_interface.is_null_session(sess):
                app.save_session(sess, resp)
            headers = resp.get_wsgi_headers(c.request.environ)
            self.cookie_jar.extract_wsgi(c.request.environ, headers)
    
    def open(self, *args, **kwargs):
        kwargs.setdefault('environ_overrides', {}) \
            ['flask._preserve_context'] = self.preserve_context
        as_tuple = kwargs.pop('as_tuple', False)
        buffered = kwargs.pop('buffered', False)
        follow_redirects = kwargs.pop('follow_redirects', False)
        builder = make_test_environ_builder(self.application, *args, **kwargs)

        return Client.open(self, builder,
                           as_tuple=as_tuple,
                           buffered=buffered,
                           follow_redirects=follow_redirects)

    def __enter__(self):
        if self.preserve_context:
            raise RuntimeError('Cannot nest client invocations')
        self.preserve_context = True
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.preserve_context = False
        top = _request_ctx_stack.top
        if top is not None and top.preserved:
            top.pop()

