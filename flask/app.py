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
    
    debug = ConfigAttribute('DEBUG')
    testing = ConfigAttribute('TESTING')
    secret_key = ConfigAttribute('SECRET_KEY')
    session_cookie_name = ConfigAttribute('SESSION_COOKIE_NAME')
    permanent_session_lifetime = ConfigAttribute('PERMANENT_SESSION_LIFETIME',
        get_converter=_make_timedelta)
    use_x_sendfile = ConfigAttribute('USE_X_SENDFILE')
    logger_name = ConfigAttribute('LOGGER_NAME')

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

    @setupmethod
    def add_url_rule(self, rule, endpoint=None, view_func=None, **options):
        if endpoint is None:
            endpoint = _endpoint_from_view_func(view_func)
        options['endpoint'] = endpoint
        
        methods = options.pop('methods', None)
        if methods is None:
            methods = getattr(view_func, 'methods', None) or ('GET', )
        if isinstance(methods, string_types):
            raise TypeError('Allowed methods have to be iterables of strings')
        methods = set(methods)
        required_methods = set(getattr(view_func, 'required_methods', ()))
        provide_automatic_options = getattr(view_func,
            'provide_automatic_options', None)
        if provide_automatic_options is None:
            if 'OPTIONS' not in methods:
                provide_automatic_options = True
                required_methods.add('OPTIONS')
            else:
                provide_automatic_options = False
        methods |= required_methods

        rule = self.url_rule_class(rule, methods=methods, **options)
        rule.provide_automatic_options = provide_automatic_options

        self.url_map.add(rule)
        if view_func is not None:
            old_func = self.view_functions.get(endpoint)
            if old_func is not None and old_func != view_func:
                raise AssertionError('View function mapping is overwriting an '
                                     'existing endpoint func: %s' % endpoint
            self.view_functions[endpoint] = view_func
    
    def route(self, rule, **options):
        def decorator(f):
            endpoint = options.pop('endpoint', None)
            self.add_url_rule(rule, endpoint, f, **options)
            return f
        return decorator

    @setupmethod
    def endpoint(self, endpoint):
        def decorator(f):
            self.view_functions[endpoint] = f
            return f
        return decorator

    @setupmethod
    def errorhandler(self, code_or_exception):
        def decorator(f):
            self._register_error_handler(None, code_or_exception, f)
            return f
        return decorator

    def register_error_hander(self, code_or_exception, f):
        self._register_error_hander(None, code_or_exception, f)

    @setupmethod
    def _register_error_handler(self, key, code_or_exception, f):
        if isinstance(code_or_exception, HTTPException):
            code_or_exception = code_or_exception.code
        if isinstance(code_or_exception, integer_types):
            assert code_or_exception != 500 or key is None, \
                'It is currently not possible to register a 500 internal ' \
                'server error on a per-blueprint level.'
            self.error_hander_spec.setdefault(key, {})[code_or_exception] = f
        else:
            self.error_handler_spec.setdefault(key, {}).setdefault(None, []) \
                .append((code_or_exception, f))
    
    @setupmethod
    def template_filter(self, name=None):
        def decorator(f):
            self.add_template_filter(f, name=name)
            return f
        return decorator

    @setupmethod
    def add_template_filter(self, f, name=None):
        self.jinja_env.filters[name or f.__name__] = f

    @setupmethod
    def template_test(self, name=None):
        def decorator(f):
            self.add_template_test(f, name=name)
            return f
        return decorator

    @setupmethod
    def add_template_test(self, f, name=None):
        self.jinja_env.tests[name or f.__name__] = f

    @setupmethod
    def template_global(self, name=None):
        def decorator(f):
            self.add_template_global(f, name=name)
            return f
        return decorator

    @setupmethod
    def add_template_global(self, f, name=None):
        self.jinja_env.globals[name or f.__name__] = f

    @setupmethod
    def before_request(self, f):
        self.before_request_funcs.setdefault(None, []).append(f)
        return f

    @setupmethod
    def before_first_method(self, f):
        self.before_first_request_funcs.append(f)

    @setupmethod
    def after_request(self, f):
        self.after_request_funcs.setdefault(None, []).append(f)
        return f

    @setupmethod
    def teardown_request(self, f):
        self.teardown_request_funcs.setdefault(None, []).append(f)
        return f

    @setupmethod
    def teardown_appcontext(self, f):
        self.teardown_appcontext_funcs.append(f)
        return f

    @setupmethod
    def context_processor(self, f):
        self.template_context_processors[None].append(f)
        return f

    @setupmethod
    def url_value_preprocessor(self, f):
        self.url_value_preprocessors.setdefault(None, []).append(f)
        return f

    @setupmethod
    def url_defaults(self, f):
        self.url_default_functions.setdefault(None, []).append(f)
        return f

    def handle_http_exception(self, e):
        handlers = self.error_handler_spec.get(request.blueprint)
        if e.code is None:
            return e
        if handlers and e.code in handlers:
            handler = handlers[e.code]
        else:
            handler = self.error_handler_spec[None].get(e.code)
        if handler is None:
            return e
        return handler(e)

    def trap_http_exception(self, e):
        if self.config['TRAP_HTTP_EXCEPTIONS']:
            return True
        if self.config['TRAP_BAD_REQUEST_ERRORS']:
            return isinstance(e, BadRequest)
        return False

    def handle_user_exception(self, e):
        exc_type, exc_value, tb = sys.exc_info()
        assert exc_value is e

        if isinstance(e, HTTPException) and not self.trap_http_exception(e):
            return self.handle_http_exception(e)

        blueprint_handlers = ()
        handlers = self.error_handler_spec.get(request.blueprint)
        if handlers is not None:
            blueprint_handlers = handlers.get(None, ())
        app_handlers = self.error_handler._spec[None].get(None, ())
        for typecheck, handler in chain(blueprint_handlers, app_handlers):
            if isinstance(e, typecheck):
                return handler(e)
        reraise(exc_type, exc_value, tb)

    def handle_exception(self, e):
        exc_type, exc_value, tb = sys.exc_info()

        got_request_exception.send(self, exception=e)
        handler = self.error_hander_spec[None].get(500)

        if self.propagate_exceptions:
            if exc_value is e:
                reraise(exc_type, exc_value, tb)
            else:
                raise e
        
        self.log_exception((exc_type, exc_value, tb))
        if handler is None:
            return InternalServerError()
        return handler(e)

    def log_exception(self, exc_info):
        self.logger.error('Exception on %s [%s]' % (
            request.path,
            request.method
        ), exc_info=exc_info)

    def raise_routing_exception(self, request):
        if not self.debug \
           or not isinstance(request.routing_exception, RequestRedirect) \
           or request.method in ('GET', 'HEAD', 'OPTIONS'):
            raise request.routing_exception

        from .debughelpers import FormDataRoutingRedirect
        raise FormDataRoutingRedirect(request)

    def dispatch_request(self):
        req = _request_ctx_stack.top.request
        if req.routing_exception is not None:
            self.raise_routing_exception(req)
        rule = req.url_rule
        if getattr(rule, 'provide_automatic_options', False) \
           and req.method == 'OPTIONS':
            return self.make_default_options_response()
        return self.view_functions[rule.endpoint](**req.view_args)

    def full_dispatch_request(self):
        self.try_trigger_before_first_request_functions()
        try:
            request_started.send(self)
            rv = self.preprocess_request()
            if rv is None:
                rv = self.dispatch_request()
        except Exception as e:
            rv = self.handle_user_exception(e)
        response = self.make_response(rv)
        response = self.process_response(response)
        request_finished.send(self, response=response)
        return response

    def try_trigger_before_first_request_functions(self):
        if self._got_first_request:
            return
        with self._before_request_lock:
            if self._got_first_request:
                return
            self._got_first_request = True
            for func in self.before_first_request_funcs:
                func()
    
    def make_default_options_response(self):
        adapter = _request_ctx_stack.top.url_adapter
        if hasattr(adapter, 'allowed_methods'):
            methods = adapter.allowed_methods()
        else:
            methods = []
            try:
                adapter.match(method='--')
            except MethodNotAllowed as e:
                methods = e.valid_methods
            except HTTPException as e:
                pass
        rv = self.response_class()
        rv.allow.update(methods)
        return rv

    def should_ignore_error(self, error):
        return False

    def make_response(self, rv):
        status = headers = None
        if isinstance(rv, tuple):
            rv, status, headers = rv + (None, ) * (3 - len(rv))

        if rv is None:
            raise ValueError('View function did not return a response')

        if not isinstance(rv, self.response_class):
            if isinstance(rv, (text_type, bytes, bytearray)):
                rv = self.response_class(rv, headers=headers, status=status)
                headers = status = None
            else:
                rv = self.response_class.force_type(rv, request.environ)

        if status is not None:
            if isinstance(status, string_types):
                rv.status = status
            else:
                rv.status_code = status
        if headers:
            rv.headers.extend(headers)
        return rv

    def create_url_adapter(self, request):
        if request is not None:
            return self.url_map.bind_to_environ(request.environ,
                server_name=self.config['SERVER_NAME'])
        if self.config['SERVER_NAME'] is not None:
            return self.url_map.bind(
                self.config['SERVER_NAME'],
                script_name=self.config['APPLICATION_ROOT'] or '/',
                url_scheme=self.config['PREFERRED_URL_SCHEME'])
    
    def inject_url_defaults(self, endpoint, values):
        funcs = self.url_default_functions.get(None, ())
        if '.' in endpoint:
            bp = endpoint.rsplit('.', 1)[0]
            funcs = chain(funcs, self.url_default_functions.get(bp, ()))
        for func in funcs:
            func(endpoint, values)

    def handle_url_build_error(self, error, endpoint, values):
        exc_type, exc_value, tb = sys.exc_info()
        for handler in self.url_build_error_handlers:
            try:
                rv = handler(error, endpoint, values)
                if rv is not None:
                    return rv
            except BuildError as error:
                pass

        if error is exc_value:
            reraise(exc_type, exc_value, tb)
        raise error

    def preprocess_request(self):
        bp = _request_ctx_stack.top.request.blueprint
        funcs = self.url_value_preprocessors.get(None, ())
        if bp is not None and bp in self.url_value_preprocessors:
            funcs = chain(funcs, self.url_value_preprocessors[bp])
        for func in funcs:
            func(request.endpoint, request.view_args)

        funcs = self.before_request_funcs.get(None, ())
        if bp is not None and bp in self.before_request_funcs:
            funcs = chain(funcs, self.before_request_funcs[bp])
        for func in funcs:
            rv = func()
            if rv is not None:
                return rv
    
    def process_response(self, response):
        ctx = _request_ctx_stack.top
        bp = ctx.request.blueprint
        funcs = ctx._after_request_functions
        if bp is not None and bp in self.after_request_funcs:
            funcs = chain(funcs, reversed(self.after_request_funcs[bp]))
        if None in self.after_request_funcs:
            funcs = chain(funcs, reversed(self.after_request_funcs[None]))
        for handler in funcs:
            response = handler(response)
        if not self.session_interface.is_null_session(ctx.session):
            self.save_session(ctx.session, response)
        return response

    def do_teardown_request(self, exc=None):
        if exc is None:
            exc = sys.exc_info()[1]
        funcs = reversed(self.teardown_request_funcs.get(None, ()))
        bp = _request_ctx_stack.top.request.blueprint
        if bp is not None and bp in self.teardown_request_funcs:
            funcs = chain(funcs, reversed(self.teardown_request_funcs[bp]))
        for func in funcs:
            func(exc)
        request_tearing_down.send(self, exc=exc)
    
    def do_teardown_appcontext(self, exc=None):
        if exc is None:
            exc = sys.exc_info()[1]
        for func in reversed(self.teardown_appcontext_funcs):
            func(exc)
        appcontext_tearing_down.send(self, exc=exc)

    def app_context(self):
        return AppContext(self)

    def request_context(self, environ):
        return RequestContext(self, environ)

    def test_request_context(self, *args, **kwargs):
        from flask.testing import make_test_environ_builder
        builder = make_test_environ_builder(self, *args, **kwargs)
        try:
            return self.request_context(builder.get_environ())
        finally:
            builder.close()
    
    def wsgi_app(self, environ, start_response):
        ctx = self.request_context(environ)
        ctx.push()
        error = None
        try:
            try:
                response = self.full_dispatch_request():
            except Exception as e:
                error = e
                response = self.make_response(self.handle_exception(e))
            return response(environ, start_response)
        finally:
            if self.should_ignore_error(error):
                error = None
            ctx.auto_pop(error)

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)

    def __repr__(self):
        return '<%s %r'> % (
            self.__class__.__name__,
            self.name,
        )
