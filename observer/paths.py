"""Shared defaults for user-owned data; explicit CLI paths remain authoritative.

Native Python dependencies stay in Application Support. Importing this module
does not create directories or relocate an existing installation.
"""
from pathlib import Path


DATA_HOME = Path.home() / '.devin-task-observer'
DEFAULT_ROOT = DATA_HOME / 'state'
DEFAULT_CONFIG = DATA_HOME / 'config.json'
DEFAULT_STORE = DATA_HOME / 'observer.sqlite3'
