from __future__ import annotations
import argparse,asyncio,json
from pathlib import Path
from worldforge.benchmarks import run_benchmark
from worldforge.models import RunConfig
from worldforge.runtime import WorldForgeEngine
def main():
    p=argparse.ArgumentParser(prog="worldforge");sub=p.add_subparsers(dest="cmd",required=True);r=sub.add_parser("run");r.add_argument("--scenario",default="boss_burst");r.add_argument("--seed",type=int,default=7);b=sub.add_parser("benchmark");b.add_argument("--seeds",type=int,default=24);args=p.parse_args()
    if args.cmd=="benchmark":print(json.dumps([x.model_dump() for x in run_benchmark(args.seeds)],indent=2))
    else:engine=WorldForgeEngine(Path("outputs")/"cli.db");summary=asyncio.run(engine.run(RunConfig(scenario_id=args.scenario,seed=args.seed),demo_delay=0));print(summary.model_dump_json(indent=2))
if __name__=="__main__":main()
