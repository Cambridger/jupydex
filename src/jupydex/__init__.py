"""Jupydex: a scriptable Jupyter terminal gateway."""

from .client import (
    JupyterTerminalClient,
    ProxySupportError,
    RemoteOutcomeUnknownError,
)
from .config import Settings

__all__ = [
    "JupyterTerminalClient",
    "ProxySupportError",
    "RemoteOutcomeUnknownError",
    "Settings",
]
__version__ = "0.4.0"
