"""Run the maintained regression suite with: python -m pytest tests."""
if __name__ == '__main__':
    import subprocess
    import sys
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', 'tests']))
