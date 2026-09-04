"""Kitchen Prep Agent package.

ADK discovers ``root_agent`` from the ``agent`` module. The import is guarded so
that importing the package for local pipeline runs / tests does not require the
google-adk dependency to be installed.
"""
try:  # pragma: no cover - exercised only when google-adk is installed
    from . import agent  # noqa: F401
except Exception:  # ADK not installed in the local/offline environment
    pass
