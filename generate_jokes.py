#!/usr/bin/env python3
"""Generate one joke per article into jokes.jsonl, via OpenRouter.

The model gets only the article title + first five paragraphs — no tools, no
web access — so it cannot have seen any joke written about the event.
Requires OPENROUTER_API_KEY in the environment or in .env next to this script.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
ARTICLES = HERE / "articles.jsonl"
JOKES = HERE / "jokes.jsonl"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

MAX_WORDS = 60

PROMPT = """Below is a news story.

Write one joke about this story — the kind a stand-up comedian would tell in a \
late-night monologue the same evening. It must be very funny: actually funny, \
the kind of joke that makes the target audience laugh out loud, not merely \
smile or think "that's clever". Laughter is the only metric.

The joke must depend on the specifics of this story. A joke that could have \
been written before this event happened fails.

Hard limit: {max_words} words. A joke longer than {max_words} words is \
automatically rejected without being read. Long setups kill jokes — tight \
beats thorough.

Reply with the joke text only — no preamble, no explanation, no options.

Title: {title}

{body}"""


def api_key():
    # .env takes precedence over the environment: a project-local key should
    # win over whatever a shell profile happens to export globally.
    key = None
    if (HERE / ".env").exists():
        for line in (HERE / ".env").read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
    key = key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set — put it in .env (see .env.example) "
                 "or in the environment")
    return key


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def complete(key, model, prompt, reasoning):
    """Return the model's reply text, or None on refusal/empty output."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16000,
    }
    if reasoning != "off":
        body["reasoning"] = {"effort": reasoning}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    content = data["choices"][0]["message"].get("content") or ""
    return content.strip() or None


class HelpfulParser(argparse.ArgumentParser):
    """Print the full help text on error, not just the one-line usage."""

    def error(self, message):
        self.print_help(sys.stderr)
        sys.stderr.write(f"\nerror: {message}\n")
        sys.exit(2)


def main():
    ap = HelpfulParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  python generate_jokes.py --model anthropic/claude-opus-4.8",
    )
    ap.add_argument("--model", required=True,
                    help="OpenRouter model slug to evaluate, e.g. anthropic/claude-opus-4.8, "
                         "openai/gpt-5.6-luna, google/gemini-3.5-flash — "
                         "see https://openrouter.ai/models")
    ap.add_argument("--reasoning", choices=["low", "medium", "high", "off"], default="high",
                    help="Reasoning effort sent to the model (default: %(default)s, so all "
                         "models compete at the same effort; 'off' omits the parameter and "
                         "uses the provider default)")
    args = ap.parse_args()

    articles = read_jsonl(ARTICLES)
    done = {(j["article_id"], j["model"]) for j in read_jsonl(JOKES)}
    todo = [a for a in articles if (a["id"], args.model) not in done]
    if not articles:
        print("No articles yet. Run fetch_articles.py first.")
        return
    if not todo:
        print(f"All {len(articles)} article(s) already have a joke from {args.model}.\n"
              f"Nothing to do — rate them with rate.py, or fetch more articles, or run "
              f"with a different --model.")
        return

    key = api_key()
    effort = "provider-default" if args.reasoning == "off" else args.reasoning
    print(f"Generating jokes from {args.model} ({effort} reasoning effort) for "
          f"{len(todo)} article(s). Each request can take a minute or more.\n")
    ok = failed = 0
    with JOKES.open("a") as out:
        for i, art in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {art['title'][:56]}... ", end="", flush=True)
            prompt = PROMPT.format(title=art["title"], body="\n\n".join(art["paragraphs"]),
                                   max_words=MAX_WORDS)
            start = time.monotonic()
            try:
                joke = complete(key, args.model, prompt, args.reasoning)
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:200]
                if e.code in (401, 403):
                    sys.exit(f"\nauth error {e.code} from OpenRouter: {detail}\n"
                             f"Check OPENROUTER_API_KEY in .env")
                print(f"API error {e.code}: {detail}")
                failed += 1
                continue
            except RuntimeError as e:
                print(f"API error: {e}")
                failed += 1
                continue
            except OSError as e:
                print(f"network error: {e}")
                failed += 1
                continue
            secs = time.monotonic() - start
            if joke is None:
                # A refusal or empty reply is recorded as a permanent failure:
                # it counts against the model's score and is not retried.
                out.write(json.dumps({
                    "article_id": art["id"],
                    "model": args.model,
                    "reasoning": args.reasoning,
                    "joke": None,
                    "refused": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }) + "\n")
                out.flush()
                print(f"refused/empty — recorded as failure [{secs:.0f}s]")
                failed += 1
                continue
            words = len(joke.split())
            if words > MAX_WORDS:
                print(f"rejected, too long ({words} > {MAX_WORDS} words) [{secs:.0f}s]")
                failed += 1
                continue
            out.write(json.dumps({
                "article_id": art["id"],
                "model": args.model,
                "reasoning": args.reasoning,
                "joke": joke,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            out.flush()
            ok += 1
            print(f"ok ({words} words, {secs:.0f}s)")

    summary = f"\n{ok} joke(s) written to {JOKES.name}"
    if failed:
        summary += (f" ({failed} failed — refusals are recorded as scored failures; "
                    f"API/length errors will be retried on the next run)")
    print(summary + ". Next: python rate.py")


if __name__ == "__main__":
    main()
