"""Repository paths for the embedded worker; independent of package nesting."""
import os
from pathlib import Path


def project_root(start: str | Path | None = None) -> Path:
    explicit = os.environ.get('SGZ_PROJECT_ROOT')
    if explicit:
        return Path(explicit).resolve()
    anchor = Path(start or __file__).resolve()
    for parent in anchor.parents:
        if (parent / 'server/package.json').is_file() and (parent / 'server/workers/python').is_dir():
            return parent
    # Installed wheels are also usable from the repository working directory.
    return Path.cwd().resolve()
