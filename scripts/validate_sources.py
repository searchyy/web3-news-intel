from __future__ import annotations

import argparse

from app.core.config import load_sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="sources.yaml")
    args = parser.parse_args()
    sources_file = load_sources(args.path)
    print(f"loaded={len(sources_file.sources)} enabled={len(sources_file.enabled_sources())}")


if __name__ == "__main__":
    main()
