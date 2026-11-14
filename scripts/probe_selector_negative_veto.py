"""Run the explicit second-round protocol, without changing production policy."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from research.selector_applicability.veto import run_veto_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(run_veto_study(args.output_dir))
    print(json.dumps({key: value for key, value in result.items() if key not in {"cases", "source_sha256"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
