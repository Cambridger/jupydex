"""Jupydex: a scriptable Jupyter terminal gateway."""

from .client import JupyterTerminalClient, RemoteOutcomeUnknownError
from .config import Settings

__all__ = [
    "JupyterTerminalClient",
    "RemoteOutcomeUnknownError",
    "Settings",
]
__version__ = "0.3.0"
