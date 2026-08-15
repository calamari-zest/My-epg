#!/usr/bin/env python3
"""
merge_epg.py

Pulls multiple free XMLTV EPG sources and merges them into ONE file:
  - Channels are deduped by normalized name.
  - If two sources have the same channel, we keep the one with an
    icon/logo if the other lacks one.
  - Programmes are unioned by channel id; if the same channel id
    exists in multiple sources, we take programmes from the source
    that has the most complete data for that channel (most fields
    filled in: descriptions, icons, categories, episode numbers).

This runs unattended in GitHub Actions — no local machine, no
tuner connection needed. Just edit SOURCES below to add/remove feeds.
"""

import gzip
import io
import xml.etree.ElementTree as ET
import requests

# ================== SOURCES ==================
# Add or remove any public XMLTV .xml or .xml.gz URL here.
# The more you add, the better the merge coverage/quality gets.

SOURCES = [
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz",
]

OUTPUT_PATH = "guide.xml"

# ================== END CONFIG ==================


def fetch(url: str):
    print(f"Fetching {url}")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    content = resp.content
    if url.endswith(".gz"):
        content = gzip.decompress(content)
    root = ET.fromstring(content)
    print(f"  -> {len(root.findall('channel'))} channels, "
          f"{len(root.findall('programme'))} programmes")
    return root


def normalize(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def score_channel(ch_el) -> int:
    """Rough 'richness' score so we can prefer the better duplicate."""
    score = 0
    if ch_el.find("icon") is not None:
        score += 5
    score += len(ch_el.findall("display-name"))
    return score


def score_programme(prog_el) -> int:
    score = 0
    for tag in ("desc", "icon", "category", "episode-num", "sub-title", "credits"):
        if prog_el.find(tag) is not None:
            score += 2
    return score


def merge(roots):
    # id -> (channel_element, source_index, score)
    best_channel = {}
    # normalized name -> id  (to detect same channel under different ids)
    name_to_id = {}

    for src_idx, root in enumerate(roots):
        for ch_el in root.findall("channel"):
            ch_id = ch_el.get("id")
            names = [dn.text for dn in ch_el.findall("display-name") if dn.text]
            if not names:
                continue
            norm = normalize(names[0])

            # if we've seen this channel name before under a different id,
            # reuse the original id so programmes line up correctly
            canonical_id = name_to_id.get(norm, ch_id)
            name_to_id.setdefault(norm, canonical_id)

            s = score_channel(ch_el)
            if canonical_id not in best_channel or s > best_channel[canonical_id][2]:
                best_channel[canonical_id] = (ch_el, src_idx, s, ch_id)

    # Now merge programmes per canonical channel id.
    # For each canonical channel, pick programmes from whichever source
    # has the richest data set for THAT channel's original id.
    merged_programmes = {}  # canonical_id -> list of programme elements

    for norm, canonical_id in name_to_id.items():
        _, _, _, original_id = best_channel[canonical_id]

        best_source_progs = []
        best_source_score = -1
        for root in roots:
            progs = root.findall(f"./programme[@channel='{original_id}']")
            if not progs:
                continue
            total = sum(score_programme(p) for p in progs)
            avg = total / len(progs)
            if avg > best_source_score:
                best_source_score = avg
                best_source_progs = progs

        merged_programmes[canonical_id] = best_source_progs

    return best_channel, merged_programmes


def build_output(best_channel, merged_programmes, out_path):
    tv = ET.Element("tv")

    for canonical_id, (ch_el, src_idx, score, original_id) in best_channel.items():
        new_ch = ET.SubElement(tv, "channel", {"id": canonical_id})
        for dn in ch_el.findall("display-name"):
            e = ET.SubElement(new_ch, "display-name")
            e.text = dn.text
        for icon in ch_el.findall("icon"):
            new_ch.append(icon)

    for canonical_id, progs in merged_programmes.items():
        for p in progs:
            # re-point programme's channel attr at the canonical id
            p.set("channel", canonical_id)
            tv.append(p)

    tree = ET.ElementTree(tv)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    print(f"\nWrote merged guide -> {out_path}")
    print(f"  Channels: {len(best_channel)}")
    print(f"  Programmes: {sum(len(v) for v in merged_programmes.values())}")


def main():
    roots = []
    for url in SOURCES:
        try:
            roots.append(fetch(url))
        except Exception as e:
            print(f"  !! Skipping {url}: {e}")

    if not roots:
        raise SystemExit("No sources could be fetched — aborting.")

    best_channel, merged_programmes = merge(roots)
    build_output(best_channel, merged_programmes, OUTPUT_PATH)


if __name__ == "__main__":
    main()
