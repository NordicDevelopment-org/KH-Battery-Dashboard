"""Run the bench:  python -m kh_bench  (then open http://localhost:8000)"""

import argparse
import logging
import os

import uvicorn

from .config import config_path, load_config


def main() -> None:
    ap = argparse.ArgumentParser(description="KH Battery discharge bench")
    ap.add_argument("--config", help="path to bench config JSON")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    if args.config:
        os.environ["KH_BENCH_CONFIG"] = args.config
    cfg = load_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Config: %s", config_path())
    from .api import create_app
    # access_log off: the dashboard polls every second, which buries real events.
    # Run starts/results/webhook errors are still logged by the engine.
    uvicorn.run(create_app(cfg), host=cfg.bench.host, port=args.port or cfg.bench.port,
                access_log=False)


if __name__ == "__main__":
    main()
