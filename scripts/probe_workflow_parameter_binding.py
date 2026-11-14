"""Run the offline parameter-binding diagnosis once in a new evidence directory."""
import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from research.parameter_binding.probe import run_probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    runner_hash = sha256(Path(__file__).read_bytes()).hexdigest()
    result = asyncio.run(run_probe(args.output_dir))
    if sha256(Path(__file__).read_bytes()).hexdigest() != runner_hash:
        raise RuntimeError("runner source changed during execution")
    result["runner_sha256"] = runner_hash
    with (args.output_dir / "report.json").open("xb") as handle:
        handle.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
