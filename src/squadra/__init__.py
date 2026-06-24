"""squadra — deterministic machinery for an AFK, board-driven Claude fleet (ADR-0007).

The supervisor tick and the per-slice runner plumbing, plus the
status-file/heartbeat convention they share. Held to the same strict typing and
lint standards as production code.

squadra operates *on* a target repository identified by ``FLEET_HOME`` (the
current working directory by default); the installed package itself is never the
working repo. The shell glue it drives ships as package data under
``squadra/_scripts`` and is resolved through :mod:`squadra._resources`.
"""
