"""Observation of the local desktop: windows/monitors, screen capture, UIA
structure and bounded snapshots. Everything here is read-only; actions live in
``spaider_foldable.agent``.

Windows only. On other platforms the module functions return empty results and
``available()`` is False.
"""
