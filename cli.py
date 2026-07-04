"""CLI: python cli.py "AI market in Central Asia" [--depth quick|medium|deep]"""
from __future__ import annotations

import argparse

from agent import ResearchAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Web research agent")
    parser.add_argument("topic", help="Topic to research")
    parser.add_argument("--depth", choices=["quick", "medium", "deep"], default="medium")
    args = parser.parse_args()

    print(f"🔍 Researching: {args.topic} (depth: {args.depth})\n")
    agent = ResearchAgent()
    result = agent.research(args.topic, args.depth)

    print("=" * 60)
    print(result["report"])
    if result["notes"]:
        print(f"\n📝 Notes saved: {len(result['notes'])}")


if __name__ == "__main__":
    main()
