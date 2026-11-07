from __future__ import annotations

import json
import sys

from .executor import execute_task_with_backend


def main() -> None:
    payload = json.loads(sys.stdin.buffer.read())
    result = execute_task_with_backend(payload)
    sys.stdout.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()