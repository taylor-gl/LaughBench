#!/usr/bin/env python3
"""Fetch recent news articles (title + first 5 paragraphs) into articles.jsonl.

Sources: NPR (text.npr.org) and CNN (lite.cnn.com) — both serve plain HTML.
Articles at or before --cutoff (the evaluated model's training cutoff) are
rejected. Each article is screened interactively so tragedies can be excluded.
"""
import argparse
import hashlib
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ARTICLES = Path(__file__).parent / "articles.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (LaughBench article fetcher)"}

# section name -> (NPR topic feed id, CNN URL path segments; None = all)
SECTIONS = {
    "news":     ("1001", None),
    "world":    ("1004", {"world", "europe", "asia", "middleeast", "americas", "china", "india"}),
    "politics": ("1014", {"politics"}),
    "business": ("1006", {"business", "economy"}),
    "tech":     ("1019", {"tech"}),
    "science":  ("1007", {"science", "climate"}),
    "health":   ("1128", {"health"}),
    "culture":  ("1008", {"entertainment", "style", "media"}),
    "sport":    (None, {"sport"}),
}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def paragraphs(raw):
    out = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", raw, re.S):
        t = html.unescape(re.sub(r"<[^>]+>", " ", p))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append(t)
    return out


NPR_DATE = re.compile(r"^\w+day, \w+ \d+, \d{4}")
NPR_CHROME = re.compile(r"^(Text-Only Version|NPR >|By |Heard on )")


def npr_body(raw):
    """Body paragraphs of a text.npr.org page: after the dateline, before 'Topics'."""
    paras = paragraphs(raw)
    start = 0
    for i, p in enumerate(paras):
        if NPR_DATE.match(p):
            start = i + 1
            break
    body = []
    for p in paras[start:]:
        if p == "Topics" or p.startswith("©"):
            break
        if NPR_CHROME.match(p):
            continue
        body.append(p)
    return body


def npr_candidates(section, feed_id):
    feed = get(f"https://feeds.npr.org/{feed_id}/rss.xml")
    for m in re.finditer(r"<item>(.*?)</item>", feed, re.S):
        item = m.group(1)
        link = re.search(r"<link>([^<]+)</link>", item)
        pub = re.search(r"<pubDate>([^<]+)</pubDate>", item)
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        if not (link and pub and title):
            continue
        sid = re.search(r"/([a-z0-9]+-s\d+-\d+)/", link.group(1))
        if not sid:
            continue
        yield {
            "source": "npr",
            "section": section,
            "url": f"https://text.npr.org/{sid.group(1)}",
            "title": html.unescape(title.group(1)).strip(),
            "published": parsedate_to_datetime(pub.group(1)),
        }


def cnn_body(raw):
    """Body paragraphs of a lite.cnn.com page: everything after the 'Source:' line."""
    paras = paragraphs(raw)
    for i, p in enumerate(paras):
        if p.startswith("Source:"):
            return paras[i + 1:]
    return []


def cnn_candidates(section, cnn_paths):
    index = get("https://lite.cnn.com/")
    seen = set()
    for m in re.finditer(r'href="(/(\d{4})/(\d{2})/(\d{2})/([a-z-]+)/[^"]+)"[^>]*>(.*?)</a>',
                         index, re.S):
        path, y, mo, d, segment, anchor = m.groups()
        if path in seen or (cnn_paths is not None and segment not in cnn_paths):
            continue
        seen.add(path)
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", anchor))).strip()
        if not title:
            continue
        yield {
            "source": "cnn",
            "section": section,
            "url": f"https://lite.cnn.com{path}",
            "title": title,
            "published": datetime(int(y), int(mo), int(d), tzinfo=timezone.utc),
        }


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
        epilog="example:\n  python fetch_articles.py --cutoff 2026-03-01 --limit 20",
    )
    ap.add_argument("--cutoff", required=True, metavar="YYYY-MM-DD",
                    help="The evaluated model's training cutoff date. Only articles "
                         "published after this date are kept, so the model cannot have "
                         "seen any joke about the event. Pick a conservative recent date "
                         "if the exact cutoff is unpublished.")
    ap.add_argument("--limit", type=int, default=20, help="Stop after N accepted articles")
    ap.add_argument("--sections", default="news", metavar="A,B,...",
                    help=f"Comma-separated topic sections to pull from "
                         f"(default: %(default)s; available: {', '.join(SECTIONS)})")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the interactive screen and accept everything (testing only — "
                         "real runs must screen out tragedies)")
    args = ap.parse_args()
    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    existing = set()
    if ARTICLES.exists():
        for line in ARTICLES.read_text().splitlines():
            existing.add(json.loads(line)["url"])

    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    unknown = [s for s in sections if s not in SECTIONS]
    if unknown:
        ap.error(f"unknown section(s): {', '.join(unknown)} "
                 f"(available: {', '.join(SECTIONS)})")

    print(f"Fetching article lists from NPR and CNN ({', '.join(sections)})...", flush=True)
    candidates, seen_urls = [], set()
    for section in sections:
        feed_id, cnn_paths = SECTIONS[section]
        found = []
        if feed_id:
            found += list(npr_candidates(section, feed_id))
        found += list(cnn_candidates(section, cnn_paths))
        for c in found:
            if c["url"] not in seen_urls:
                seen_urls.add(c["url"])
                candidates.append(c)
    print(f"{len(candidates)} candidate(s) found.\n")

    accepted = 0
    skipped = {"at/before cutoff": 0, "already fetched": 0, "under 5 paragraphs": 0,
               "declined": 0, "fetch failed": 0}
    with ARTICLES.open("a") as out:
        for cand in candidates:
            if accepted >= args.limit:
                break
            if cand["published"] <= cutoff:
                skipped["at/before cutoff"] += 1
                continue
            if cand["url"] in existing:
                skipped["already fetched"] += 1
                continue
            existing.add(cand["url"])
            try:
                raw = get(cand["url"])
            except Exception as e:
                print(f"  skip (fetch failed: {e}): {cand['url']}", file=sys.stderr)
                skipped["fetch failed"] += 1
                continue
            body = npr_body(raw) if cand["source"] == "npr" else cnn_body(raw)
            if len(body) < 5:
                skipped["under 5 paragraphs"] += 1
                continue
            if not args.yes:
                print(f"\n[{cand['source']}/{cand['section']}] {cand['title']}")
                ans = input("  Include? (y = yes / n = no, e.g. tragedy / q = quit) ").strip().lower()
                if ans == "q":
                    break
                if ans != "y":
                    skipped["declined"] += 1
                    continue
            out.write(json.dumps({
                "id": hashlib.sha1(cand["url"].encode()).hexdigest()[:12],
                "source": cand["source"],
                "section": cand["section"],
                "url": cand["url"],
                "published": cand["published"].isoformat(),
                "title": cand["title"],
                "paragraphs": body[:5],
            }) + "\n")
            accepted += 1
            print(f"  accepted ({accepted}/{args.limit}): {cand['title']}")

    print(f"\n{accepted} article(s) added to {ARTICLES.name}")
    reasons = ", ".join(f"{v} {k}" for k, v in skipped.items() if v)
    if reasons:
        print(f"(skipped: {reasons})")
    if accepted < args.limit and skipped["at/before cutoff"] and not accepted:
        print("Everything was at/before the cutoff — these feeds only carry recent "
              "stories, so try again later or use an earlier --cutoff if appropriate.")
    if accepted:
        print("Next: python generate_jokes.py --model <slug>")


if __name__ == "__main__":
    main()
