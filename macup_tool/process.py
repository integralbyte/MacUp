from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Mapping

from .logutil import redact


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    output: str


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult):
        super().__init__(f"command failed with exit code {result.returncode}: {' '.join(result.args)}")
        self.result = result


def run_streamed(
    args: list[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    logger=None,
    check: bool = True,
) -> CommandResult:
    if logger:
        logger.write(f"$ {' '.join(args)}")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        env=dict(env or os.environ),
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        clean = redact(line.rstrip("\n"))
        lines.append(clean)
        if logger:
            logger.write(clean)
    process.stdout.close()
    returncode = process.wait()
    output = "\n".join(lines)
    result = CommandResult(args=args, returncode=returncode, output=output)
    if check and returncode != 0:
        raise CommandError(result)
    return result
