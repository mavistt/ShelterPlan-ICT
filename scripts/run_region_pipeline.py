"""Run the config-driven Module A v2 pipeline for one region."""
import argparse
import json
import sys

from module_a_v2_lib import run_region


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region-id", required=True)
    parser.add_argument("--reuse-legacy-network", action="store_true", help="East-only fallback to the frozen v0.1 graph")
    parser.add_argument("--skip-network", action="store_true", help="Skip blocked public Overpass/OD work and keep non-network outputs")
    args = parser.parse_args()
    result = run_region(args.region_id, force_legacy_network=args.reuse_legacy_network, skip_network=args.skip_network)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"DEMO_ONLY", "PASS"} else 1


if __name__ == "__main__":
    sys.exit(main())
