#!/usr/bin/env python3
"""Interactive rating session: read the article, see the joke, record a/b/c.

  a — didn't laugh
  b — laughed, but the joke was generic and could have pre-existed the event
  c — laughed, and the joke was likely new and original
"""
import json
import random
from datetime import datetime, timezone
from pathlib import Path

ARTICLES = Path(__file__).parent / "articles.jsonl"
JOKES = Path(__file__).parent / "jokes.jsonl"
RATINGS = Path(__file__).parent / "ratings.jsonl"


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    articles = {a["id"]: a for a in read_jsonl(ARTICLES)}
    jokes = read_jsonl(JOKES)
    if not jokes:
        print("No jokes yet. Run generate_jokes.py first.")
        return
    rated = {(r["article_id"], r["model"]) for r in read_jsonl(RATINGS)}
    todo = [j for j in jokes
            if j.get("joke")  # refusals are scored automatically, nothing to rate
            and (j["article_id"], j["model"]) not in rated and j["article_id"] in articles]
    if not todo:
        print(f"All {len(jokes)} joke(s) are already rated. Run score.py for the "
              "results, or fetch more articles / generate with another --model.")
        return
    random.shuffle(todo)

    print(f"{len(todo)} joke(s) to rate. For each: read the story, press ENTER "
          "to see the joke, then answer a/b/c. Quit anytime with q — progress "
          "is saved after every rating.\n")
    n = 0
    with RATINGS.open("a") as out:
        for idx, joke in enumerate(todo, 1):
            art = articles[joke["article_id"]]
            print("=" * 72)
            print(f"\n({idx}/{len(todo)}) {art['title']}\n")
            for p in art["paragraphs"]:
                print(f"{p}\n")
            try:
                input("[ENTER to see the joke] ")
            except EOFError:
                break
            print(f"\n>>> {joke['joke']}\n")
            ans = None
            while ans not in ("a", "b", "c", "q"):
                try:
                    ans = input("(a) didn't laugh  (b) laughed, but generic — could have "
                                "pre-existed the event  (c) laughed, new and original  "
                                "(q) quit: ").strip().lower()
                except EOFError:
                    ans = "q"
            if ans == "q":
                break
            out.write(json.dumps({
                "article_id": joke["article_id"],
                "model": joke["model"],
                "rating": ans,
                "rated_at": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            out.flush()
            n += 1
            print()

    print(f"\n{n} rating(s) saved to {RATINGS.name}")
    if n:
        print("Next: python score.py")


if __name__ == "__main__":
    main()
