from collections.abc import Callable
# kicadstamp/cli_common.py
"""
cli_common.py — the single owner of CLI exit codes.

Every CLI front-end (kicadstamp_cli.py's ``main()`` and ``author.cli_main()``
used by ``boards/*/scripts/*.py``) must translate exceptions into process
exit codes. That translation used to be copy-pasted in two places; this
module is the one source of truth so every front-end reports errors
identically and a change to the contract lands in exactly one file.

This is deliberately the ONLY module that owns exit codes. Library code must
never ``sys.exit()`` — it should raise ``PlacerError``/``ValidationError``/
``ApiError`` and let the CLI front-end (via :func:`run_cli`) decide the
exit code and the message.
"""
import logging


from kipy.errors import ApiError, ApiStatusCode

from .exceptions import PlacerError
from .i18n import _


def api_error_message(e: ApiError) -> str:
    """Human-readable message for a KiCad IPC :class:`ApiError`.

    ``AS_BUSY`` gets the long "finish the tool in KiCad" explanation — the
    most common real-world cause and the easiest to misread as a hang. Every
    other code gets a straightforward "KiCad returned API error: ...".
    """
    if e.code == ApiStatusCode.AS_BUSY:
        return _(
            "KiCad is busy and cannot respond right now. Usually this means an unfinished "
            "tool is running in the GUI (dimensioning, interactive routing, move tool, etc.) — "
            "finish it (Esc or right-click -> Cancel) and run the command again. "
            "The board was not modified."
        )
    return _("KiCad returned API error: {e}").format(e=e)


def run_cli(main_fn: Callable[[], None]) -> int:
    """Run a CLI body and translate exceptions into a process exit code.

    *main_fn* — a zero-arg callable with the command's real work. It must
    NOT ``sys.exit()`` itself for its own errors; ``run_cli`` does the
    translation. A ``SystemExit`` raised *inside* ``main_fn`` (e.g. by
    argparse on bad flags, or a deliberately aborted run) is a
    ``BaseException`` and propagates unchanged with its own exit code.

    Returns the exit code:
      - ``0`` on success;
      - ``1`` for ``PlacerError``/``ValidationError`` (expected user-facing
        fatal: bad config, ambiguous role, unknown ``--only``/``--cluster``)
        and for ``ApiError`` (KiCad IPC failure; ``AS_BUSY`` gets a
        dedicated message);
      - ``2`` for any other ``Exception`` (unexpected bug — full traceback).

    The caller decides how to propagate it — ``sys.exit(run_cli(fn))`` or
    ``return run_cli(fn)`` from its own ``main()``.
    """
    try:
        main_fn()
    except PlacerError as e:
        logging.error(_("Error: {e}").format(e=e))
        return 1
    except ApiError as e:
        logging.error(api_error_message(e))
        return 1
    except Exception:
        logging.exception(_("Unexpected error"))
        return 2
    return 0
