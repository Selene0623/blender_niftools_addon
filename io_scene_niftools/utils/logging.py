""" Nif Utilities, stores logging across the code base"""

# ***** BEGIN LICENSE BLOCK *****
#
# Copyright © 2016, NIF File Format Library and Tools contributors.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#    * Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials provided
#      with the distribution.
#
#    * Neither the name of the NIF File Format Library and Tools
#      project nor the names of its contributors may be used to endorse
#      or promote products derived from this software without specific
#      prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# ***** END LICENSE BLOCK *****

import inspect
import logging
import os

from io_scene_niftools.utils.consts import LOGGER_PYFFI, LOGGER_PLUGIN


_file_handler = None


class _MockOperator:
    """A default implementation of the report function in the case where the operator has not been initialised."""

    def report(self, level, message):
        print(f"{level}: {message}")


class NifLog:
    """A simple custom exception class for export errors. This module require initialisation of an operator reference to function."""

    # Injectable operator reference used to perform reporting, default to simple logging
    op = _MockOperator()

    @staticmethod
    def _report(level, message):
        """Call op.report() safely, falling back to MockOperator if the operator has been destroyed."""
        try:
            NifLog.op.report(level, message)
        except ReferenceError:
            _MockOperator().report(level, message)

    @staticmethod
    def debug(message):
        """Report a debug message."""
        NifLog._report({'DEBUG'}, str(message))
        logging.getLogger("niftools").debug(str(message))

    @staticmethod
    def info(message):
        """Report an informative message."""
        NifLog._report({'INFO'}, str(message))
        logging.getLogger("niftools").info(str(message))

    @staticmethod
    def warn(message):
        """Report a warning message."""
        NifLog._report({'WARNING'}, str(message))
        logging.getLogger("niftools").warning(str(message))

    @staticmethod
    def error(message):
        """Report an error and return ``{'FINISHED'}``. To be called by
        the :meth:`execute` method, as::

            return error('Something went wrong.')

        Blender will raise an exception that is passed to the caller.

        .. seealso::

            The :ref:`error reporting <dev-design-error-reporting>` design.
        """
        NifLog._report({'ERROR'}, message)
        logging.getLogger("niftools").error(str(message))
        return {'FINISHED'}

    @staticmethod
    def init(operator):
        global _file_handler

        NifLog.op = operator

        niftools_level_num = getattr(logging, operator.properties.plugin_log_level)
        logging.getLogger(LOGGER_PLUGIN).setLevel(niftools_level_num)

        pyffi_level_num = getattr(logging, operator.properties.pyffi_log_level)
        logging.getLogger(LOGGER_PYFFI).setLevel(pyffi_level_num)

        # manage optional file logging
        log_path = getattr(operator.properties, "log_file_path", "")
        niftools_logger = logging.getLogger(LOGGER_PLUGIN)

        if _file_handler is not None:
            niftools_logger.removeHandler(_file_handler)
            _file_handler.close()
            _file_handler = None

        if log_path:
            log_dir = os.path.dirname(log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            _file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            _file_handler.setLevel(niftools_level_num)
            file_formatter = logging.Formatter("%(asctime)s:%(name)s:%(levelname)s:%(message)s")
            _file_handler.setFormatter(file_formatter)
            niftools_logger.addHandler(_file_handler)


class NifError(Exception):
    """A simple custom exception class for export errors."""
    def __init__(self, msg):
        caller = inspect.getframeinfo(inspect.stack()[1][0])
        NifLog.error(f"{msg:s}")
        NifLog.error(f"{caller.filename:s}:{caller.lineno:d}")
    pass


def init_loggers():
    """Set up loggers."""
    log_handler = logging.StreamHandler()
    log_handler.setLevel(logging.DEBUG)
    log_formatter = logging.Formatter("%(name)s:%(levelname)s:%(message)s")
    log_handler.setFormatter(log_formatter)

    create_logger(log_handler, LOGGER_PYFFI)
    create_logger(log_handler, LOGGER_PLUGIN)


def create_logger(log_handler, name):
    pyffi_logger = logging.getLogger(name)
    pyffi_logger.setLevel(logging.WARNING)
    pyffi_logger.addHandler(log_handler)
