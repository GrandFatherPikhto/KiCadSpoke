# kicadstamp/utils/paths.py

from pathlib import Path, PureWindowsPath


def resolve_config_relative_path(base_dir: Path, raw: str) -> str:
    """Resolves a path from a YAML config value against the config file's
    directory, unless ``raw`` is already absolute.

    ``Path(base_dir) / raw`` only recognizes ``raw`` as absolute per the
    current OS's flavor: on POSIX it discards ``base_dir`` for a leading
    ``/``, but a Windows-style absolute path (``C:/tmp/run.log``) has no
    leading ``/`` and gets silently joined onto ``base_dir`` instead of kept
    as-is. Checking both flavors here keeps config values portable across
    the OS that authored them and the OS that loads them.
    """
    if Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        return str(Path(raw))
    return str(base_dir / raw)
