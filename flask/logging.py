# -*- coding: utf-8 -*-
"""
    flask.logging
    ~~~~~~~~~~~~~

    Implements the logging support for Flask.
"""

from __future__ import absolute_import
from logging import getLogger, StreamHandler, Formatter, getLoggerClass, DEBUG


def create_logger(app):
    Logger = getLoggerClass()

    class DebugLogger(Logger):
        def getEffectiveLevel(x):
            if x.level == 0 and app.debug:
                return DEBUG
            return Logger.getEffectiveLevel(x)

    class DebugHandler(StreamHandler):
        def emit(x, record):
            StreamHandler.emit(x, record) if app.debug else None

    handler = DebugHandler()
    handler.setLevel(DEBUG)
    handler.setFormatter(Formatter(app.debug_log_format))
    logger = getLogger(app.logger_name)
    del logger.handlers[:]
    logger.__class__ = DebugLogger
    logger.addHandler(handler)
    return logger
