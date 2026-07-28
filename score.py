#!/usr/bin/env python3
"""Print the LaughBench score per model: fraction of jokes rated (c).

Refusals count as failures: a model that answers "I refuse to joke about that"
has produced a legitimately unfunny response, so it lands in the denominator
with zero credit, exactly like a joke nobody laughed at.
"""
import json
from collections import defaultdict
from pathlib import Path

JOKES = Path(__file__).parent / "jokes.jsonl"
RATINGS = Path(__file__).parent / "ratings.jsonl"


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    ratings = read_jsonl(RATINGS)
    refused = defaultdict(int)
    for j in read_jsonl(JOKES):
        if j.get("refused"):
            refused[j["model"]] += 1
    if not ratings and not refused:
        print("No ratings yet. Run rate.py first.")
        return

    by_model = defaultdict(list)
    for r in ratings:
        by_model[r["model"]].append(r["rating"])
    for model in sorted(set(by_model) | set(refused)):
        rs = by_model.get(model, [])
        c = rs.count("c")
        total = len(rs) + refused[model]
        note = f" ({refused[model]} refusal(s) counted as failures)" if refused[model] else ""
        print(f"{model}: {c}/{total} = {c / total:.1%}{note}")


if __name__ == "__main__":
    main()
