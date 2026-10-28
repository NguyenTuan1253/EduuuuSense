"""Shared shell command execution helper for execution adapters."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def run_shell_cmd(cmd: list[str] | str, cwd: Path | None = None, timeout: int = 600) -> tuple[int, str, str]:
    """Execute a command and return (exit_code, stdout, stderr).

    Prepends backend/.venv executables to PATH so pytest/ruff/mypy resolve
    without activation, matching workflow.py find_tool behavior.
    """
    env = os.environ.copy()
    venv_scripts = REPO_ROOT / "backend" / ".venv" / "Scripts"
    if venv_scripts.exists():
        env["PATH"] = f"{venv_scripts};{env.get('PATH', '')}"

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=isinstance(cmd, str),
            env=env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def run_verification_commands(commands: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[list, bool]:
    """Run declared verification commands and collect VerificationResult records.

    Returns (results, all_passed). Imported lazily to avoid a circular import
    with models at module load time in type checkers.
    """
    from ..models import VerificationResult

    results = []
    all_passed = True
    for v_cmd in commands:
        code, stdout, stderr = run_shell_cmd(v_cmd, cwd=cwd, timeout=timeout)
        passed = (code == 0)
        if not passed:
            all_passed = False
        results.append(
            VerificationResult(
                command=v_cmd,
                exit_code=code,
                stdout=stdout,
                stderr=stderr,
                passed=passed,
            )
        )
    return results, all_passed
