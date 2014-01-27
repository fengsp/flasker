# -*- coding: utf-8 -*-
"""
    flask.jsonimpl
    ~~~~~~~~~~~~~~

    Implementation helpers for the JSON support in Flask.
"""
import io
import uuid
from datetime import datetime

from werkzeug.http import http_date
from jinja2 import Markup
try:
    from itsdangerous import simplejson as _json
except ImportError:
    from itsdangerous import json as _json

from .globals import current_app, request


_slash_escape = '\\/' not in _json.dumps('/')


__all__ = ['dump', 'dumps', 'load', 'loads', 'htmlsafe_dump',
           'htmlsafe_dumps', 'JSONDecoder', 'JSONEncoder',
           'jsonify']


def _wrap_reader_for_text(fp, encoding):
    if isinstance(fp.read(0), bytes):
        fp = io.TextIOWrapper(io.BufferedReader(fp), encoding)
    return fp


def _wrap_writer_for_text(fp, encoding):
    try:
        fp.write('')
    except TypeError:
        fp = io.TextIOWrapper(fp, encoding)
    return fp


class JSONEncoder(_json.JSONEncoder):
    
    def default(self, o):
        if isinstance(o, datetime):
            return http_date(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        if hasattr(o, '__html__'):
            return unicode(o.__html__())
        return _json.JSONEncoder.default(self, o)


class JSONDecoder(_json.JSONDecoder):
    """The default JSON decoder."""


def _dump_arg_defaults(kwargs):
    if current_app:
        kwargs.setdefault('cls', current_app.json_encoder)
        if not current_app.config['JSON_AS_ASCII']:
            kwargs.setdefault('ensure_ascii', False)
        kwargs.setdefault('sort_keys', current_app.config['JSON_SORT_KEYS'])
    else:
        kwargs.setdefault('sort_keys', True)
        kwargs.setdefault('cls', JSONEncoder)


def _load_arg_defaults(kwargs):
    if current_app:
        kwargs.setdefault('cls', current_app.json_encoder)
    else:
        kwargs.setdefault('cls', JSONDecoder)


def dumps(obj, **kwargs):
    _dump_arg_defaults(kwargs)
    encoding = kwargs.pop('encoding', None)
    rv = _json.dumps(obj, **kwargs)
    if encoding is not None and isinstance(rv, unicode):
        rv = rv.encode(encoding)
    return rv


def dump(obj, fp, **kwargs):
    _dump_arg_defaults(kwargs)
    encoding = kwargs.pop('encoding', None)
    if encoding is not None:
        fp = _wrap_writer_for_text(fp, encoding)
    _json.dump(obj, fp, **kwargs)


def loads(s, **kwargs):
    _load_arg_defaults(kwargs)
    if isinstance(s, bytes):
        s = s.decode(kwargs.pop('encoding', None) or 'utf-8')
    return _json.loads(s, **kwargs)


def load(fp, **kwargs):
    _load_arg_defaults(kwargs)
    return _json.load(fp, **kwargs)


def htmlsafe_dumps(obj, **kwargs):
    rv = dumps(obj, **kwargs) \
        .replace(u'<', u'\\u003c') \
        .replace(u'>', u'\\u003e') \
        .replace(u'&', u'\\u0026') \
        .replace(u"'", u'\\u0027')
    if not _slash_escape:
        rv = rv.replace('\\/', '/')
    return rv


def htmlsafe_dump(obj, fp, **kwargs):
    fp.write(unicode(htmlsafe_dumps(obj, **kwargs)))


def jsonify(*args, **kwargs):
    indent = None
    if current_app.config['JSONIFY_PRETTYPRINT_REGULAR'] \
       and not request.is_xhr:
        indent = 2
    return current_app.response_class(dumps(dict(*args, **kwargs),
        indent=indent),
        mimetype='application/json')


def tojson_filter(obj, **kwargs):
    return Markup(htmlsafe_dumps(obj, **kwargs))

