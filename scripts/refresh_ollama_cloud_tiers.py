#!/usr/bin/env python3
"""Re-derive Ollama Cloud usage tiers from ollama.com.

`OllamaCloudProvider.USAGE_TIERS` drives the order of the model picker, and it
matters because most Finn users are on a free Ollama account: a "High usage"
model burns their allowance far faster than a "Low usage" one, and a metered
model bills them outright. That table is a hand-maintained snapshot, so this
script exists to check it rather than trust it.

Neither `https://ollama.com/v1/models` nor `/api/tags` carries tier or pricing,
so the facts have to come off each model's public page:

  * the cloud tag line — "<tag>-cloud  Low Usage · 256K context window · Text, Image"
  * the pricing block on metered models — "Cost /1M tokens $3.00 input … $15.00 output"
  * the capability chips — vision / tools / thinking

Usage (no API key needed — all of this is public):

    python scripts/refresh_ollama_cloud_tiers.py           # diff against the code
    python scripts/refresh_ollama_cloud_tiers.py --print   # emit a pasteable table

Exits non-zero when the live site disagrees with `USAGE_TIERS`, so it can be
wired into CI later if the drift ever gets expensive.
"""
from __future__ import annotations

import argparse
import html as htmllib
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ai_service import OllamaCloudProvider  # noqa: E402

CATALOGUE_URL = "https://ollama.com/v1/models"
PAGE_URL = "https://ollama.com/library/{base}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; finn-tier-refresh)"}

MODALITY_MAP = {"Text": "text", "Image": "vision", "Audio": "audio"}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _visible_text(markup: str) -> str:
    markup = re.sub(r"<script.*?</script>|<style.*?</style>", " ", markup, flags=re.S)
    return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)))


def live_catalogue() -> list[str]:
    import json

    return sorted(m["id"] for m in json.loads(_get(CATALOGUE_URL))["data"])


def scrape(tag: str) -> tuple[str, str, str] | None:
    """Return (usage_tier, context, modalities) for one cloud tag."""
    text = _visible_text(_get(PAGE_URL.format(base=tag.split(":")[0])))

    if re.search(r"Cost\s*/\s*1M tokens", text):
        ctx = re.search(r"Context\s+([\d.]+[KM])\s+tokens", text)
        mods = _modalities_from_chips(text)
        return ("metered", ctx.group(1) if ctx else "?", mods)

    # "<tag>-cloud  <Tier> Usage · <ctx> context window · <modalities> ·"
    # Models with size variants suffix the cloud tag (`gemma4:31b-cloud`);
    # single-variant models use a `:cloud` tag instead (`glm-5.1:cloud`).
    # Tier alternation is longest-first so "Extra High" can't match as "High".
    hit = re.search(
        re.escape(tag) + r"[-:]cloud\s+(Extra High|Very High|Low|Medium|High) Usage"
        r"\s*·\s*([\d.]+[KM]) context window\s*·\s*([A-Za-z, ]+?)\s*·",
        text,
    )
    if not hit:
        return None
    modalities = " + ".join(
        MODALITY_MAP.get(p.strip(), p.strip().lower())
        for p in hit.group(3).split(",")
        if p.strip()
    )
    return (hit.group(1), hit.group(2), modalities)


def _modalities_from_chips(text: str) -> str:
    chips = re.search(r"Cancel((?:\s+(?:vision|tools|thinking|cloud))+)\s", text)
    return "text + vision" if chips and "vision" in chips.group(1) else "text"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", dest="emit",
                    help="print a pasteable USAGE_TIERS table")
    args = ap.parse_args()

    known = OllamaCloudProvider.USAGE_TIERS
    tags = live_catalogue()
    print(f"{len(tags)} cloud models live on ollama.com\n")

    scraped: dict[str, tuple[str, str, str]] = {}
    unreadable: list[str] = []
    for tag in tags:
        try:
            fact = scrape(tag)
        except Exception as exc:  # network / markup change
            print(f"  ! {tag}: {exc}")
            unreadable.append(tag)
            continue
        if fact is None:
            unreadable.append(tag)
        else:
            scraped[tag] = fact

    if args.emit:
        width = max(len(t) for t in scraped) + 3
        print("    USAGE_TIERS = {")
        order = {t: i for i, t in enumerate(OllamaCloudProvider._TIER_ORDER)}
        for tag, (usage, ctx, mods) in sorted(
            scraped.items(), key=lambda kv: (order.get(kv[1][0], 99), kv[0])
        ):
            cell = f'"{tag}":'.ljust(width)
            print(f'        {cell}("{usage}",'.ljust(width + 22)
                  + f'"{ctx}",'.ljust(9) + f'"{mods}"),')
        print("    }")
        print()

    drift = 0
    for tag, fact in sorted(scraped.items()):
        if tag not in known:
            print(f"  NEW      {tag}: {fact}")
            drift += 1
        elif tuple(known[tag]) != fact:
            print(f"  CHANGED  {tag}: {tuple(known[tag])} -> {fact}")
            drift += 1
    for tag in sorted(set(known) - set(tags)):
        print(f"  GONE     {tag} is no longer in the catalogue")
        drift += 1
    for tag in sorted(unreadable):
        print(f"  UNKNOWN  {tag}: no tier line found (page markup may have changed)")

    if drift:
        print(f"\n{drift} difference(s) vs OllamaCloudProvider.USAGE_TIERS.")
        return 1
    print("\nUSAGE_TIERS matches ollama.com.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
