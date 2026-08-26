#!/usr/bin/env python3
"""Make a tool's output survive being redirected.

Python hands a Windows *console* UTF-8, but a *redirected* stdout gets the
system ANSI code page instead -- cp1251 on this machine, cp1252 on a western
install. Neither can encode a status label carrying an emoji or a company name
carrying a diacritic, so a tool that prints perfectly well in a terminal dies
with UnicodeEncodeError the moment its output is piped: into a file, into
another program, or into the agent that called it. That last one is how most
of these tools are actually read, which is why this is not cosmetic.

Each tool calls use_utf8() from its `__main__` block rather than on import,
because this is startup behaviour for a program -- not something a module
should do to whoever imports it, tests included.
"""

from __future__ import annotations

import sys


def use_utf8() -> None:
    """Force stdout and stderr to UTF-8, replacing what will not encode.

    errors="replace" rather than the default "strict": a tool reporting on
    somebody's CV should render one character badly rather than abort with a
    traceback halfway through the report.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # a stream with no reconfigure is not worth failing over
