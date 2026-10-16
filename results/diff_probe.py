"""Diff two probe dumps so the visual change can be stated in computed values, not adjectives.

    python results/diff_probe.py results/ui/before-probe results/ui/review-probe

Each argument is a *prefix*: the per-page JSONs are `<prefix>-<page>.json`. For every selector the
first page that has a non-null entry supplies the value, so one table covers chat, admin,
compliance and ROI without hand-picking a page per row.
"""
import json
import pathlib
import sys

PAGES = ["chat", "admin", "compliance", "roi"]

# The properties worth reading out loud. Anything not in here is noise for this comparison.
KEYS = ["n", "bg", "color", "radius", "border", "weight"]

# Only the selectors whose value a reader can see, in the order they appear on screen.
WORTH_SHOWING = [
    '[data-testid="stSidebar"]',
    '[data-testid="stSidebar"] h1',
    '[data-testid="stSidebar"] .stButton > button',
    '[data-testid="stAlertContainer"]',
    '[data-testid="stChatMessageAvatarUser"]',
    '[data-testid="stChatMessageAvatarAssistant"]',
    '[data-testid="stChatInput"] button',
    '[data-testid="stMarkdownContainer"] a',
    '[data-testid="stMarkdownContainer"] code',
    '[data-testid="stCaptionContainer"]',
    '[data-testid="stDataFrame"]',
    '[data-testid="stMetricValue"]',
    'button[kind="secondary"]',
    'button[kind="primary"]',
    '[data-testid="stExpander"] summary',
]


def load(prefix):
    merged = {}
    for page in PAGES:
        path = pathlib.Path(f"{prefix}-{page}.json")
        if not path.exists():
            continue
        for sel, info in json.loads(path.read_text()).items():
            if info and info != "BAD SELECTOR" and sel not in merged:
                merged[sel] = info
    return merged


def main():
    before = load(sys.argv[1])
    after = load(sys.argv[2])

    rows = 0
    print(f"{'selector':<48} {'property':<8} {'before':<34} after")
    print("-" * 120)
    for sel in WORTH_SHOWING:
        b, a = before.get(sel), after.get(sel)
        if not b or not a:
            print(f"{sel:<48} {'--':<8} {'(missing on one side)':<34}")
            continue
        for key in KEYS:
            bv, av = str(b.get(key)), str(a.get(key))
            if bv == av:
                continue
            print(f"{sel:<48} {key:<8} {bv:<34} {av}")
            rows += 1
    print(f"\n{rows} differing computed properties")


if __name__ == "__main__":
    main()
