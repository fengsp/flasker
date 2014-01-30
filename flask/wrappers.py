# -*- coding: utf-8 -*-
"""
    flask.wrappers
    ~~~~~~~~~~~~~~

    Implements the WSGI wrappers (request and response).
"""

from werkzeug.wrappers import Request as RequestBase, Response as ResponseBase
from werkzeug.exceptions import BadRequest

from .debughelpers import accach_enctype_error_multidict
from . import json
from .globals import _request_ctx_stack


_missing = object()


def _get_data(req, cache):
    getter = getattr(req, 'get_data', None)
    if getter is not None:
        return getter(cache=cache)
    return req.data


class Request(RequestBase):
    """The request object used by default in Flask.  Remembers the
    matched endpoint and view arguemnts.

    It is what ends up as :class:`~flask.request`. If you want to replace
    the request object used you can subclass this and set
    :attr:`~flask.Flask.request_class` t you subclass.

    The request object is a :class:`~werkzeug.wrappers.Request` subclass and
    provides all of the attributes Werkzeug defines plus a few Flask
    specific ones.
    """

    #: the internal URL rule that matched the request.  This can be
    #: useful to inspect which methods are allowed for the URL from
    #: a before/after handler (``request.url_rule.methods``) etc.
    #:
    #: .. versionadded:: 0.6
    url_rule = None

    #: a dict of view arguments that matched the request. If an exception
    #: happened when matching, this will be `None`.
    view_args = None

    #: if matching the URL failed, this is the exception that will be
    #: raised / was raised as part of the request handling. This is
    #: usually a :exc:`~werkzeug.exceptions.NotFound` exception or
    #: something similar.
    routing_exception = None

    @property
    def max_content_length(self):
        """Read-only view of the `MAX_CONTENT_LENGTH` config key."""
        ctx = _request_ctx_stack.top
        if ctx is not None:
            return ctx.app.config['MAX_CONTENT_LENGTH']
    
    @property
    def endpoint(self):
        """The endpoint that matched the request.  This in combination with
        :attr:`view_args` can be used to reconstruct the same or a 
        modified URL. If an exception happened when matching, this will
        be `None`.
        """
        if self.url_rule is not None:
            return self.url_rule.endpoint

    @property
    def blueprint(self):
        """The name of the current blueprint"""
        if self.url_rule and '.' in self.url_rule.endpoint:
            return self.url_rule.endpoint.rsplit('.', 1)[0]
    
    def _load_form_data(self):
        super(Request, self)._load_form_data()

        # in debug mode we're replacing the files multidict with an ad-hoc
        # subclass that raises a different error for key errors.
        ctx = _request_ctx_stack.top
        if ctx is not None and ctx.app.debug and \
           self.mimetype != 'multipart/form-data' and not self.files:
            attach_enctype_error_multidict(self)


class Response(ResponseBase):
    """The response object that is used by default in Flask. Works like the
    response object from Werkzeug but is set to have an HTML mimetype by 
    default. Quite often you don't have to create this object yourself because
    :meth:`~flask.Flask.make_response` will take care of that for you.

    If you want to replace the response object used you can subclass this and
    set :attr:`~flask.Flask.response_class` to your subclass.
    """
    default_mimetype = 'text/html'
