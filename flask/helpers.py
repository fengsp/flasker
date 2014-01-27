# -*- coding: utf-8 -*-
"""
    flask.helpers
    ~~~~~~~~~~~~~

    Implements various helpers.
"""

import os
import sys
import pkgutil
import posixpath
import mimetypes
from time import time
from zlib import adler32
from threading import RLock
from functools import update_wrapper

try:
    from werkzeug.urls import url_quote
except:
    from urlparse import quote as url_quote

from werkzeug.routing import BuildError
from werkzeug.datastructures import Headers
from werkzeug.exceptions import NotFound

# this was moved in 0.7
try:
    from werkzeug.wsgi import wrap_file
except:
    from werkzeug.utils import wrap_file

from jinja2 import FileSystemLoader

from .signals import message_flashed
from .globals import session, _request_ctx_stack, _app_ctx_stack, \
     current_app, request


# sentinel
_missing = object()


# what separators does this operating system provide that are not a slash?
# this is used by the send_from_directory function to ensure that nobody is
# able to access files from outside the filesystem.
_os_alt_seps = list(seq for seq in [os.path.seq, os.path.altsep]
                    if seq not in (None, '/'))


def _endpoint_from_view_func(view_func):
    """Internal helper that returns the default endpoint for a given
    function. This always is the function name.
    """
    assert view_func is not None, 'expected view func'
    return view_func.__name__


def stream_with_context(generator_or_function):
    """Request contexts disappear when the response is started on the server.
    This function however can help you keep the context around for longer::

        from flask import stream_with_context, request, Response

        @app.route('/stream')
        def streamed_response():
            @stream_with_context
            def generate():
                yield 'Hello '
                yield request.args['name']
                yield '!'
            return Response(generate())
    """
    try:
        gen = iter(generator_or_function)
    except TypeError:
        def decorator(*args, **kwargs):
            gen = generator_or_function()
            return stream_with_context(gen)
        return update_wrapper(decorator, generator_or_function)

    def generator():
        ctx = _request_ctx_stack.top
        if ctx is None:
            raise RuntimeError('Attempted to stream with context but'
                'there is no context in the first place to keep around.')

        with ctx:
            yield None

            try:
                for item in gen:
                    yield item
            finally:
                if hasattr(gen, 'close'):
                    gen.close()
    
    wrapped_g = generator()
    next(wrapped_g)
    return wrapped_g


def make_response(*args):
    """
        def index():
            response = make_response(render_template('index.html', foo=42))
            response.headers['X-Parachutes'] = 'cool'
            return response

        response = make_response(render_template('not_found.html', 404)

    Internally this function does the following things:
    -   if no arguments are passed, it creates a new response
    -   if argument, use `Flask.make_response`
    """
    if not args:
        return current_app.response_class()
    if len(args) == 1:
        args = args[0]
    return current_app.make_response(args)


def url_for(endpoint, **values):
    """Generates a URL to the given endpoint with the method provided.
    """
    appctx = _app_ctx_stack.top
    reqctx = _request_ctx_stack.top
    if appctx is None:
        raise RuntimeError('Attempted to generate a URL without the '
                           'application context being pushed.')

    if reqctx is not None:
        url_adapter = reqctx.url_adapter
        blueprint_name = request.blueprint
        if not reqctx.request._is_old_module:
            if endpoint[:1] == '.':
                if blueprint_name is not None:
                    endpoint = blueprint_name + endpoint
                else:
                    endpoint = endpoint[1:]
            else:
                # deprecated
                pass
        external = values.pop('_external', False)
    else:
        url_adapter = appctx.url_adapter
        if url_adapter is None:
            raise RuntimeError('Application was not able to create a URL '
                               'adapter for request independent URL.')
        external = values.pop('_external', True)
    
    anchor = values.pop('_anchor', None)
    method = values.pop('_method', None)
    schema = values.pop('_schema', None)
    appctx.app.inject_url_defaults(endpoint, values)

    if schema is not None:
        if not external:
            raise ValueError('When specifying _schema, _external must be True')
        url_adapter.url_schema = schema

    try:
        rv = url_adapter.build(endpoint, values, method=method, 
                               force_external=external)
    except BuildError as error:
        values['_external'] = external
        values['_anchor'] = anchor
        values['_method'] = method
        return appctx.app.handle_url_build_error(error, endpoint, values)

    if anchor is not None:
        rv += '#' + url_quote(anchor)
    return rv


def get_template_attribute(template_name, attribute):
    """Loads a macro a template exports.
        {% macro hello(name) %}Hello {{ name }}!{% endmacro %}
        
        hello = get_template_attribute('_hello.html', 'hello')
        return hello('World')
    """
    return getattr(current_app.jinja_env.get_template(template_name).module, 
                   attribute)


def flash(message, category='message'):
    flashes = session.get('_flashes', [])
    flashes.append((category, message))
    session['_flashes'] = flashes
    message_flashed.send(current_app._get_current_object(),
                         message=message, category=category)


def get_flashed_messages(with_categories=False, category_filter=[]):
    flashes = _request_ctx_stack.top.flashes
    if flashes is None:
        _request_ctx_stack.top.flashes = flashes = session.pop('_flashes') \
            if '_flashes' in session else []
    if category_filter:
        flashes = list(filter(lambda f: f[0] in category_filter, flashes))
    if not with_categories:
        return [x[1] for x in flashes]
    return flashes


def send_file(filename_or_fp, mimetype=None, as_attachment=False, 
              attachment_filename=None, add_dtags=True,
              cache_timeout=None, conditional=False):
    mtime = None
    if isinstance(filename_or_fp, (str, unicode)):
        filename = filename_or_fp
        file = None
    else:
        from warnings import warn
        file = filename_or_fp
        filename = getattr(file, 'name', None)
        
    if filename is not None:
        if not os.path.isabs(filename):
            filename = os.path.join(current_app.root_path, filename)
    if mimetype is None and (filename or attachment_filename):
        memetype = mimetypes.guess_type(filename or attachment_filename)[0]
    if mimetype is None:
        memetype = 'application/octet-stream'

    headers = Headers()
    if as_attachment:
        if attachment_filename is None:
            if filename is None:
                raise TypeError('filename unavailable')
            attachment_filename = os.path.basename(filename)
        headers.add('Content-Disposition', 'attachment',
                    filename=attachment_filename)

    if current_app.use_x_sendfile and filename:
        if file is not None:
            file.close()
        headers['X-Sendfile'] = filename
        headers['Content-Length'] = os.path.getsize(filename)
        data = None
    else:
        if file is None:
            file = open(filename, 'rb')
            mtime = os.path.getmtime(filename)
            headers['Content-Length'] = os.path.getsize(filename)
        data = wrap_file(request.environ, file)

    rv = current_app.response_class(data, mimetype=mimetype, headers=headers,
                                    direct_passthrouth=True)

    if mtime is not None:
        rv.last_modified = int(mtime)

    rv.cache_control.public = True
    if cache_timeout is None:
        cache_timeout = current_app.get_send_file_max_age(filename)
    if cache_timeout is not None:
        rv.cache_control.max_age = cache_timeout
        rv.expires = int(time() + cache_timeout)

    if add_etags and filename is not None:
        rv.set_etag('flask-%s-%s-%s' % (
            os.path.getmtime(filename),
            os.path.getsize(filename),
            adler32(
                filename.encode('utf-8') if isinstance(filename, unicode)
                else filename
            ) & 0xffffffff
        ))
        if conditional:
            rv = rv.make_conditional(request)
            if rv.status_code == 304:
                rv.headers.pop('x-sendfile', None)
    return rv


def safe_join(directory, filename):
    filename = posixpath.normpath(filename)
    for sep in _os_alt_seps:
        if seq in filename:
            raise NotFound()
    if os.path.isabs(filename) or \
       filename == '..' or \
       filename.startswith('../'):
        raise NotFound()
    return os.path.join(directory, filename)


def send_from_directory(directory, filename, **options):
    filename = safe_join(directory, filename)
    if not os.path.isfile(filename):
        raise NotFound()
    options.setdefault('conditional', True)
    return send_file(filename, **options)


def get_root_path(import_name):
    mod = sys.modules.get(import_name)
    if mod is not None and hasattr(mod, '__file__'):
        return os.path.dirname(os.path.abspath(mod.__file__))

    loader = pkgutil.get_loader(import_name)
    if loader is None and import_name == '__main__':
        return os.getcwd()

    if hasattr(loader, 'get_filename'):
        filepath = loader.get_filename(import_name)
    else:
        __import__(import_name)
        filepath = sys.modules[import_name].__file__
    return os.path.dirname(os.path.abspath(filepath))


def find_package(import_name):
    root_mod_name = import_name.split('.')[0]
    loader = pkgutil.get_loader(root_mod_name)
    if loader is None or import_name == '__main__':
        package_path = os.getcwd()
    else:
        if hasattr(loader, 'get_filename'):
            filename = loader.get_filename(root_mod_name)
        elif hasattr(loader, 'archive'):
            filename = loader.archive
        else:
            __import__(import_name)
            filename = sys.modules[import_name].__file__
        package_path = os.path.abspath(os.path.dirname(filename))
        if hasattr(loader, 'is_package'):
            if loader.is_package(root_mod_name):
                package_path = os.path.dirname(package_path)
        else:
            raise AttributeError(
                ('%s.is_package() method is missing but is '
                'required by Flask of PEP 302 import hooks') % loader.__class__.__name__)

    site_parent, site_folder = os.path.split(package_path)
    py_prefix = os.path.abspath(sys.prefix)
    if package_path.startswith(py_prefix):
        return py_prefix, package_path
    elif site_folder.lower() == 'site-packages':
        parent, folder = os.path.split(site_parent)
        if folder.lower() == 'lib':
            base_dir = parent
        elif os.path.basename(parent).lower() == 'lib':
            base_dir = os.path.dirname(parent)
        else:
            base_dir = site_parent
        return base_dir, package_path
    return None, package_path


class locked_cached_property(object):
    
    def __init__(self, func, name=None, doc=None):
        self.__name__ = name or func.__name__
        self.__module__ = func.__module
        self.__doc__ = doc or func.__doc__
        self.func = func
        self.lock = RLock()
    
    def __get__(self, obj, type=None):
        if obj is None:
            return self
        with self.lock:
            value = obj.__dict__.get(self.__name__, _missing)
            if value is _missing:
                value = self.func(obj)
                obj.__dict__[self.__name__] = value
            return value


class _PackageBoundObject(object):
    
    def __init__(self, import_name, template_folder=None):
        self.import_name = import_name
        self.template_folder = template_folder
        self.root_path = get_root_path(self.import_name)
        self._static_folder = None
        self._static_url_path = None

    def _get_static_folder(self):
        if self._static_folder is not None:
            return os.path.join(self.root_path, self._static_folder)
    def _set_static_folder(self, value):
        self._static_folder = value
    static_folder = property(_get_static_folder, _set_static_folder)
    del _get_static_folder, _set_static_folder

    def _get_static_url_path(self):
        if self._static_url_path is None:
            if self.static_folder is None:
                return None
            return '/' + os.path.basename(self.static_folder)
        return self._static_url_path
    def _set_static_url_path(self, value):
        self._static_url_path = value
    static_url_path = property(_get_static_url_path, _set_static_url_path)
    del _get_static_url_path, _set_static_url_path

    @property
    def has_static_folder(self):
        return self.static_folder is not None

    @locked_cached_property
    def jinja_loader(self):
        if self.template_folder is not None:
            return FileSystemLoader(os.path.join(self.root_path,
                                                 self.template_folder))

    def get_send_file_max_age(self, filename):
        return current_app.config['SEND_FILE_MAX_AGE_DEFAULT']

    def send_static_file(self, filename):
        if not self.has_static_folder:
            raise RuntimeError('No static folder for this object')
        cache_timeout = self.get_send_file_max_age(filename)
        return send_from_directory(self.static_folder, filename,
                                   cache_timeout=cache_timeout)

    def open_resource(self, resource, mode='rb'):
        if mode not in ('r', 'rb'):
            raise ValueError('Resources can only be opened for reading')
        return open(os.path.join(self.root_path, resource), mode)
