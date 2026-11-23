"""Re-run only the region-scoped Module A v2 spatial analysis layers."""
import argparse
import json
import sys

from module_a_v2_lib import load_config, run_spatial_analysis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region-id", required=True)
    args = parser.parse_args()
    result = run_spatial_analysis(args.region_id, load_config())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
