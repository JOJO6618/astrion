"""Shared lifecycle labels for the resumable child runtime.

Idle is a live, resumable instance. Only explicit termination is irreversible.
Persistence and restoration live in persistence.py and manager.py.
"""

TERMINAL_STATUSES = {"failed", "terminated"}
