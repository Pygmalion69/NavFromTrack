#!/usr/bin/env python3
"""Project entry point."""

import sys

from gpx_osm_to_directions import main as gpx_osm_to_directions_main


def main() -> int:
    return gpx_osm_to_directions_main()


if __name__ == "__main__":
    sys.exit(main())
