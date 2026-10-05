#!/usr/bin/env python3
"""Launch native acceptance in an isolated source or frozen-app process.

Uses local 9B/27B fixtures, no global hotkeys or paid search. Examples:
  python3 scripts/harness_acceptance.py --only restart_and_readonly_history
  python3 scripts/harness_acceptance.py --packaged dist/AI桌面助手.app --output /tmp/acceptance.json
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE = 'ai_desktop.diagnostics.harness_acceptance'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packaged', type=Path, help='App bundle or packaged executable to verify')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model', help='Local fixture model: 9B (default) or qwen3.8:27b-mlx')
    parser.add_argument('--only', help='One acceptance case; see module --help for available cases')
    args = parser.parse_args(argv)
    command = [sys.executable, '-m', MODULE]
    if args.packaged:
        executable = args.packaged.expanduser().resolve()
        if executable.suffix == '.app':
            executable /= 'Contents/MacOS/AI桌面助手'
        if not executable.is_file() or not os.access(executable, os.X_OK):
            parser.error('Packaged executable does not exist or is not executable')
        command = [str(executable), '--harness-acceptance']
    if args.output:
        command += ['--output', str(args.output.expanduser().resolve())]
    if args.model:
        command += ['--model', args.model]
    if args.only:
        command += ['--only', args.only]
    with tempfile.TemporaryDirectory(prefix='aide-acceptance-') as directory:
        root = Path(directory).resolve()
        environ = dict(os.environ, AIDE_ACCEPTANCE_ROOT=str(root),
                       AIDE_DATA_DIR=str(root/'data'), AIDE_LOG_DIR=str(root/'logs'))
        return subprocess.run(command, cwd=REPO, env=environ, check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
