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
from ._compat import string_types, text_type


# sentinel

