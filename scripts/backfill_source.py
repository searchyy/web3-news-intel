from __future__ import annotations

import argparse

from app.workers.tasks_fetch import fetch_source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_key")
    args = parser.parse_args()
    print(fetch_source(args.source_key))


if __name__ == "__main__":
    main()
