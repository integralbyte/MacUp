from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_text_atomic(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, content)
