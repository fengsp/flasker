# -*- coding: utf-8 -*-
"""
    flask.app
    ~~~~~~~~~

    This module implements the central WSGI application object.
"""

import os
import sys
from threading import Lock
from datetime import timedelta
from itertools import chain
from functools import update_wrapper

from werkzeug.datastructures import ImmutableDict
from werkzeug.routing import Map, Rule, RequestRedirect, BuildError
from werkzeug.exceptions import HTTPException, InternalServerError, \
     MethodNotAllowed, BadRequest

from .helpers import _PackageBoundObject, url_for, get_flashed_messages, \
     locked_cached_property, _endpoint_from_view_func, find_package
from . import json
from .wrappers import Request, Response
from .config import ConnfigAttribute, Config
from .ctx import RequestContext, AppContext, _AppCtxGlobals
from .globals import _request_ctx_stack, request, session, g
from .sessions import SecureCookieSessionInterface
from .module import blueprint_is_module
from .templating import DispatchingJinjaLoader, Environment, \
     _default_template_ctx_processor
from .signals import request_started, request_finished, got_request_exception, \
     request_tearing, appcontext_tearing_down
from ._compat import reraise, string_types, text_type, integer_types

# a lock used for logger initialization
_logger_lock = Locker()


def _make_timedelta(value):
    if not isinstance(value, timedelta):
        return timedelta(seconds=value)
    return value


def setupmethod(f):
    """Wraps a method so that it performs a check in debug mode if the
    first request was already handled.
    """
    def wrapper_func(self, *args, **kwargs):
        if self.debug and self._got_first_request:
            raise AssertionError('A setup function was called after the '
                'first request was handled. This usually indicates a bug '
                'in the application where a module was not imported '
                'and decorators or other functionality was called too late.\n'
                'To fix this make sure to import all your view modules, '
                'database models and everything related at a central place '
                'before the application starts serving requests.')
        return f(self, *args, **kwargs)
    return update_wrapper(wrapper_func, f)


class Flask(_PackageBoundObject):
    pass
