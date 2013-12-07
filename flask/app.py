# -*- coding: utf-8 -*-
"""
    flask.app
    ~~~~~~~~~

    this module implements the central wsgi application object.
"""

import os
import sys
from threading import lock
from datetime import timedelta
from itertools import chain
from functools import update_wrapper

from werkzeug.datastructures import immutabledict
from werkzeug.routing import map, rule, requestredirect, builderror
from werkzeug.exceptions import httpexception, internalservererror, \
     methodnotallowed, badrequest

from .helpers import _packageboundobject, url_for, get_flashed_messages, \
     locked_cached_property, _endpoint_from_view_func, find_package
from . import json
from .wrappers import request, response
from .config import connfigattribute, config
from .ctx import requestcontext, appcontext, _appctxglobals
from .globals import _request_ctx_stack, request, session, g
from .sessions import securecookiesessioninterface
from .module import blueprint_is_module
from .templating import dispatchingjinjaloader, environment, \
     _default_template_ctx_processor
from .signals import request_started, request_finished, got_request_exception, \
     request_tearing, appcontext_tearing_down
from ._compat import reraise, string_types, text_type, integer_types

# a lock used for logger initialization
_logger_lock = locker()


def _make_timedelta(value):
    if not isinstance(value, timedelta):
        return timedelta(seconds=value)
    return value


def setupmethod(f):
    """wraps a method so that it performs a check in debug mode if the
    first request was already handled.
    """
    def wrapper_func(self, *args, **kwargs):
        if self.debug and self._got_first_request:
            raise assertionerror('a setup function was called after the '
                'first request was handled. this usually indicates a bug '
                'in the application where a module was not imported '
                'and decorators or other functionality was called too late.\n'
                'to fix this make sure to import all your view modules, '
                'database models and everything related at a central place '
                'before the application starts serving requests.')
        return f(self, *args, **kwargs)
    return update_wrapper(wrapper_func, f)


class flask(_packageboundobject):
    """the flask object implements a wsgi application"""
    request_class = request
    response_class = response
    app_ctx_globals_class = _appctxglobals
    
    debug = configattribute('debug')
    testing = configattribute('testing')
    secret_key = configattribute('secret_key')
    session_cookie_name = configattribute('session_cookie_name')
    permanent_session_lifetime = configattribute('permanent_session_lifetime',
        get_converter=_make_timedelta)
    use_x_sendfile = configattribute('use_x_sendfile')
    logger_name = configattribute('logger_name')

    enable_modules = true

    debug_log_format = (
        '-' * 80 + '\n' +
        '%(levelname)s in %(module)s [%(pathname)s:%(lineno)d]:\n' +
        '%(message)s\n' +
        '-' * 80
    )

    json_encoder = json.jsonencoder
    json_decoder = json.jsondecoder

    jinja_options = immutabledict(
        extensions=['jinja2.ext.autoescape', 'jinja2.ext.with_']
    )

    #: default configuration parameters.
    default_config = immutabledict({
        'debug':                                false,
        'testing':                              false,
        'propagate_exceptions':                 none,
        'preserve_context_on_exception':        none,
        'secret_key':                           none,
        'permanent_session_lifetime':           timedelta(days=31),
        'use_x_sendfile':                       false,
        'logger_name':                          none,
        'server_name':                          none,
        'application_root':                     none,
        'session_cookie_name':                  'session',
        'session_cookie_domain':                none,
        'session_cookie_path':                  none,
        'session_cookie_httponly':              true,
        'session_cookie_secure':                false,
        'session_refresh_each_request':         true,
        'max_content_length':                   none,
        'send_file_max_age_default':            12 * 60 * 60, # 12 hours
        'trap_bad_request_errors':              false,
        'trap_http_exceptions':                 false,
        'preferred_url_scheme':                 'http',
        'json_as_ascii':                        true,
        'json_sort_keys':                       true,
        'jsonify_prettyprint_regular':          true,
    })
    
    url_rule_class = rule
    test_client_class = none
    session_interface = securecookiesessioninterface()

    def __init__(self, import_name, static_url_path=none,
                 static_folder='static', template_folder='templates',
                 instance_path=none, instance_relative_config=false):
        _packageboundobject.__init__(self, import_name,
                                     template_folder=template_folder)
        
        if static_url_path is not none:
            self.static_url_path = static_url_path
        if static_folder is not none:
            self.static_folder = static_folder
        if instance_path is none:
            instance_path = self.auto_find_instance_path()
        elif not os.path.isabs(instance_path):
            raise valueerror('if an instance path is provided it must be '
                             'absolute.  a relative path was given instead.')
        
        self.instance_path = instance_path
        
        self.config = self.make_config(instance_relative_config)
        
        self._logger = none
        self.logger_name = self.import_name

        self.view_functions = {}

        self.error_handler_spec = {none: self._error_handlers}

        self.url_build_error_handlers = []

        self.before_request_funcs = {}
        self.before_first_request_funcs = []
        self.after_request_funcs = {}
        self.teardown_request_funcs = {}
        self.teardown_appcontext_funcs = []
        
        self.url_value_preprocessors = {}
        self.url_default_functions = {}

        self.template_context_processors = {
            none: [_default_template_ctx_processor]
        }

        self.blueprints = {}

        self.extensions = {}

        self.url_map = map()

        self._got_first_request = false
        self._before_request_lock = lock()

        if self.has_static_folder:
            self.add_url_rule(self.static_url_path + '/<path:filename>',
                              endpoint='static',
                              view_func=self.send_static_file)
    
    @locked_cached_property
    def name(self):
        if self.import_name == '__main__':
            fn = getattr(sys.modules['__main__'], '__file__', none)
            if fn is none:
                return '__main__'
            return os.path.splitext(os.path.basename(fn))[0]
        return self.import_name

    @property
    def propagate_exceptions(self):
        rv = self.config['propagate_exceptions']
        if rv is not none:
            return rv
        return self.testing or self.debug

    @property
    def preserve_context_on_exception(self):
        """Returns the value of the `PRESERVE_CONTEXT_ON_EXCEPTION`
        configuration value in case it's set, otherwise a sensible default
        is returned.

        .. versionadded:: 0.7
        """
        rv = self.config['PRESERVE_CONTEXT_ON_EXCEPTION']
        if rv is not None:
            return rv
        return self.debug

    @property
    def logger(self):
        if self._logger and self._logger.name == self.logger_name:
            return self._logger
        with _logger_lock:
            if self._logger and self._logger.name == self.logger_name:
                return self._logger
            from flask.logging import create_logger
            self._logger = rv = create_logger(self)
            return rv

    @locked_cached_property
    def jinja_env(self):
        return self.create_jinja_environment()

    @property
    def got_first_request(self):
        return self._got_first_request

    def make_config(self, instance_relative=False):
        root_path = self.root_path
        if instance_relative:
            root_path = self.instance_path
        return Config(root_path, self.default_config)

    def auto_find_instance_path(self):
        prefix, package_path = find_package(self.import_name)
        if prefix is None:
            return os.path.join(package_path, 'instance')
        return os.path.join(prefix, 'var', self.name + '-instance')

    def open_instance_resource(self, resource, mode='rb'):
        return open(os.path.join(self.instance_path, resource), mode)

    def create_jinja_environment(self):
        options = dict(self.jinja_options)
        if 'autoescape' not in options:
            options['autoescape'] = self.select_jinja_autoescape
        rv = Environment(self, **options)
        rv.globals.update(
            url_for=url_for,
            get_flashed_messages=get_flashed_messages,
            config=self.config,
            request=request,
            session=session,
            g=g
        )
        rv.filters['tojson'] = json.tojson_filter
        return rv

    def create_global_jinja_loader(self):
        return DispatchingJinjaLoader(self)

    def select_jinja_autoescape(self, filename):
        if filename is None:
            return False
        return filename.endswith(('.html', '.htm', '.xml', '.xhtml'))

    def update_template_context(self, context):
        funcs = self.template_context_processors[None]
        reqctx = _request_ctx_stack.top
        if reqctx is not None:
            bp = reqctx.request.blueprint
            if bp is not None and bp in self.template_context_processors:
                funcs = chain(funcs, self.template_context_processors[bp])
        orig_ctx = context.copy()
        for func in funcs:
            context.update(func())
        context.update(orig_ctx)

    def run(self, host=None, port=None, debug=None, **options):
        from werkzeug.serving import run_simple
        if host is None:
            host = '127.0.0.1'
        if port is None:
            server_name = self.config['SERVER_NAME']
            if server_name and ':' in server_name:
                port = int(server_name.rsplit(':', 1)[1])
            else:
                port = 5000
        if debug is not None:
            self.debug = bool(debug)
        options.setdefault('use_reloader', self.debug)
        options.setdefault('use_debugger', self.debug)
        try:
            run_simple(host, port, self, **options)
        finally:
            self._got_first_request = False

    def test_client(self, use_cookie=True):
        cls = self.test_client_class
        if cls is None:
            from flask.testing import FlaskClient as cls
        return cls(self, self.response_class, use_cookies=use_cookies)

    def open_session(self, request):
        return self.session_interface.open_session(self, request)

    def save_session(self, session, response):
        return self.session_interface.save_session(self, session, response)

    def make_null_session(self):
        return self.session_interface.make_null_session(self)

    @setupmethod
    def register_blueprint(self, blueprint, **options):
        first_registration = False
        if blueprint.name in self.blueprints:
            assert self.blueprints[blueprint.name] is blueprint, \
                'A blueprint\'s name collision occurred between %r and ' \
                '%r.  Both share the same name "%s".  Blueprints that ' \
                'are created on the fly need unique names.' % \
                (blueprint, self.blueprints[blueprint.name], blueprint.name)
        else:
            self.blueprints[blueprint.name] = blueprint
            first_registration = True
        blueprint.register(self, options, first_registration)

