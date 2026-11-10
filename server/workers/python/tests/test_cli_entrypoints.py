"""CLI help must work outside the checkout without touching a game or database."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('module', ['sgz_agent', 'sgz_agent.hunt_trial'])
def test_cli_help_from_an_unrelated_directory(module, tmp_path):
    source = Path(__file__).resolve().parents[1] / 'src'
    env = {**os.environ, 'PYTHONPATH': str(source)}
    result = subprocess.run([sys.executable, '-m', module, '--help'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '--config' in result.stdout
    assert not list(tmp_path.iterdir())
