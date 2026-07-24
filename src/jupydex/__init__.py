"""Jupydex: a scriptable Jupyter terminal gateway."""

from .client import JupyterTerminalClient
from .config import Settings

__all__ = ["JupyterTerminalClient", "Settings"]
__version__ = "0.2.0"
