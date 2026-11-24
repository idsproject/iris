#!/usr/bin/env python3
"""
iris_layout.py - Build page-faithful, accessible HTML from a PDF plus the
Bedrock Data Automation (BDA) result.json produced by the ASU PDF-to-HTML
pipeline.

Why: the pipeline's remediated.html is semantically useful but looks nothing
like the original article. This post-processor keeps the semantics (reading
order, headings, tables, figures with alt text) and lays every text line out
exactly where it sits on the original page, over a text-free rendering of the
page's graphics (rules, shading, table stripes). At narrow widths or high zoom
the page view collapses into a single reflowing column (WCAG 1.4.10).

Text comes from the PDF itself (more accurate than BDA's OCR-ish HTML).
Structure comes from BDA: element types, sub_types, reading order, tables.
Alt text comes from remediated.html when it is better than nothing, otherwise
it is derived from BDA; weak alt text is flagged in the report.

Usage:
  python3 iris_layout.py ORIGINAL.pdf result.json -o out.html \
      [--remediated remediated.html] [--report report.json] [--dpi 144] \
      [--print-out out_print.html --fonts ./fonts]

--print-out writes a second file built for PrinceXML (16+) tagged-PDF output.
The file itself switches on tagging and the PDF/UA-1 profile, so it works from
the Prince desktop app or the plain command line:
  prince out_print.html -o out.pdf
In that file the visible text itself is the tagged content (no hidden
screen-reader copy, which Prince would tag twice), backgrounds, running
heads/feet and line-end hyphens are Artifacts, tables are real tables whose
cells hold the visible lines, and Liberation fonts (metric-compatible with
Arial / Times New Roman / Courier New) are embedded with every line's width
scaling precomputed, so Prince lays the page out exactly as measured.

Requires: pymupdf, beautifulsoup4, numpy, fonttools
"""
import argparse
import base64
import difflib
import html
import io
import json
import os
import re
from collections import Counter

import numpy as np
import pymupdf as fitz
from bs4 import BeautifulSoup

PT2PX = 96 / 72  # CSS px per PDF point

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def esc(s):
    return html.escape(s, quote=False)


def rect_area(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def intersect(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def contains(r, x, y, tol=2.0):
    return r[0] - tol <= x <= r[2] + tol and r[1] - tol <= y <= r[3] + tol


def text_of_html(fragment):
    return BeautifulSoup(fragment or "", "html.parser").get_text(" ", strip=True)


SCREEN_STACKS = {
    "mono": "'Courier New', Courier, monospace",
    "serif": "Georgia, 'Times New Roman', Times, serif",
    "sans": "Arial, Helvetica, 'Liberation Sans', sans-serif",
}
PRINT_FAMILIES = {"mono": "IRIS Mono", "serif": "IRIS Serif", "sans": "IRIS Sans"}


def font_key(fontname, flags):
    """Classify a PDF font: (family key, bold, italic)."""
    name = re.sub(r"^[A-Z]{6}\+", "", fontname or "")
    low = name.lower()
    serif = any(k in low for k in ("egyp", "times", "serif", "georgia", "garamond",
                                    "minion", "palatino", "cambria", "book"))
    mono = any(k in low for k in ("courier", "mono", "consol"))
    key = "mono" if mono else "serif" if serif else "sans"
    bold = bool(flags & 16) or any(k in low for k in ("bold", "semibold", "black", "heavy", "-md", "medium", "demi"))
    italic = bool(flags & 2) or bool(re.search(r"(italic|oblique|-it\b|it$)", low))
    return key, bold, italic


def font_css(fontname, flags):
    """Map a PDF font name to a CSS font stack plus weight/style."""
    key, bold, italic = font_key(fontname, flags)
    return SCREEN_STACKS[key], (700 if bold else 400), ("italic" if italic else "normal")


class PrintFonts:
    """Liberation fonts for the Prince file: measures text so each line's
    horizontal scale can be fixed at build time, then embeds subsets."""
    FILES = {"sans": "LiberationSans", "serif": "LiberationSerif", "mono": "LiberationMono"}

    def __init__(self, folder):
        from fontTools.ttLib import TTFont
        self.folder = folder
        self.fonts, self.used = {}, {}
        for key, stem in self.FILES.items():
            for bold in (False, True):
                for italic in (False, True):
                    style = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
                    path = os.path.join(folder, f"{stem}-{style}.ttf")
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"missing font {path} (needed for --print-out)")
                    f = TTFont(path)
                    self.fonts[(key, bold, italic)] = (path, f.getBestCmap(), f["hmtx"].metrics,
                                                       f["head"].unitsPerEm)

    def width(self, text, key, bold, italic, size):
        path, cmap, hmtx, upm = self.fonts[(key, bold, italic)]
        used = self.used.setdefault((key, bold, italic), set())
        w = 0.0
        for ch in text:
            g = cmap.get(ord(ch))
            if g is None:
                w += 0.5 * upm  # glyph will come from a fallback font
                continue
            used.add(ord(ch))
            w += hmtx[g][0]
        return w * size / upm

    def css(self):
        import logging
        from fontTools import subset
        logging.getLogger("fontTools").setLevel(logging.ERROR)
        rules = []
        for (key, bold, italic), chars in sorted(self.used.items()):
            path = self.fonts[(key, bold, italic)][0]
            opts = subset.Options()
            opts.layout_features = []          # no kerning/ligatures: widths stay as measured
            opts.drop_tables += ["kern", "GPOS", "GSUB"]
            opts.name_IDs = ["*"]
            opts.notdef_outline = True
            font = subset.load_font(path, opts)
            sub = subset.Subsetter(opts)
            sub.populate(unicodes=sorted(chars | {0x20, 0x2D}))
            sub.subset(font)
            buf = io.BytesIO()
            font.save(buf)
            data = base64.b64encode(buf.getvalue()).decode()
            rules.append(f"@font-face{{font-family:'{PRINT_FAMILIES[key]}';"
                         f"src:url(data:font/ttf;base64,{data}) format('truetype');"
                         f"font-weight:{700 if bold else 400};font-style:{'italic' if italic else 'normal'}}}")
        return "\n".join(rules)


def color_hex(c):
    return "#%06x" % (c & 0xFFFFFF)


# --------------------------------------------------------------------------
# contrast (WCAG 2.x relative luminance)
# --------------------------------------------------------------------------

DEFAULT_INK = 0x111111  # colour of text the PDF draws as pure black


def rgb(c):
    return ((c >> 16) & 255, (c >> 8) & 255, c & 255)


def to_int(t):
    return (int(round(t[0])) << 16) | (int(round(t[1])) << 8) | int(round(t[2]))


def rel_lum(t):
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = t
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def required_ratio(size_pt, bold):
    large = size_pt >= 18 or (bold and size_pt >= 14)
    return 3.0 if large else 4.5


def fix_color(fg, bg, need):
    """Smallest move of fg toward black (or white on dark grounds) that meets
    the ratio; keeps the hue so the page still looks like the original."""
    if contrast(fg, bg) >= need + 0.05:
        return fg
    target = (0, 0, 0) if rel_lum(fg) <= rel_lum(bg) else (255, 255, 255)
    for i in range(1, 51):
        t = i / 50
        c = tuple(f + (g - f) * t for f, g in zip(fg, target))
        if contrast(c, bg) >= need + 0.05:
            return tuple(round(v) for v in c)
    return target


# --------------------------------------------------------------------------
# PDF line extraction
# --------------------------------------------------------------------------

def extract_lines(page):
    """Return text lines with span detail, in PDF points."""
    out = []
    d = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP)
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            if tuple(round(v, 2) for v in ln["dir"]) != (1.0, 0.0):
                continue  # rotated text: skipped (reported)
            # Rebuild span text from glyphs: tightly justified PDFs often
            # position words without a space character, and line-end
            # hyphens are frequently U+00AD (invisible in browsers).
            prev_char = None
            for s in ln["spans"]:
                txt = []
                for ch in s["chars"]:
                    c = ch["c"].replace("­", "-")
                    if prev_char is not None and c != " " and prev_char["c"] != " ":
                        gap = ch["bbox"][0] - prev_char["bbox"][2]
                        if gap > s["size"] * 0.1:
                            txt.append(" ")
                    txt.append(c)
                    prev_char = ch
                s["text"] = "".join(txt)
            spans = [s for s in ln["spans"] if s["text"]]
            if not spans or not "".join(s["text"] for s in spans).strip():
                continue
            # trim leading/trailing whitespace-only spans
            while spans and not spans[0]["text"].strip():
                spans.pop(0)
            while spans and not spans[-1]["text"].strip():
                spans.pop()
            if not spans:
                continue
            x0 = min(s["bbox"][0] for s in spans)
            x1 = max(s["bbox"][2] for s in spans)
            y0 = min(s["bbox"][1] for s in spans)
            y1 = max(s["bbox"][3] for s in spans)
            sizes = Counter()
            for s in spans:
                sizes[round(s["size"], 1)] += len(s["text"])
            main = sizes.most_common(1)[0][0]
            base_span = max(spans, key=lambda s: (round(s["size"], 1) == main, len(s["text"])))
            out.append({
                "bbox": (x0, y0, x1, y1),
                "spans": spans,
                "size": main,
                "baseline": base_span["origin"][1],
                "text": "".join(s["text"] for s in spans),
            })
    # PDFs sometimes split one visual line into several "lines"; merge
    # fragments that share a baseline and nearly touch horizontally.
    out.sort(key=lambda l: (round(l["baseline"], 0), l["bbox"][0]))
    merged = []
    for l in out:
        if merged:
            m = merged[-1]
            if (abs(m["baseline"] - l["baseline"]) < 0.8 and abs(m["size"] - l["size"]) < 1.5
                    and 0 <= l["bbox"][0] - m["bbox"][2] < m["size"] * 0.9):
                gap = l["bbox"][0] - m["bbox"][2]
                if gap > m["size"] * 0.15 and not m["text"].endswith(" ") and not l["text"].startswith(" "):
                    m["spans"].append({**m["spans"][-1], "text": " ", "bbox": (m["bbox"][2], m["bbox"][1], l["bbox"][0], m["bbox"][3])})
                m["spans"].extend(l["spans"])
                m["bbox"] = (m["bbox"][0], min(m["bbox"][1], l["bbox"][1]), l["bbox"][2], max(m["bbox"][3], l["bbox"][3]))
                m["text"] = "".join(s["text"] for s in m["spans"])
                continue
        merged.append(l)
    for l in merged:
        x0, y0, x1, y1 = l["bbox"]
        l["cx"], l["cy"] = (x0 + x1) / 2, (y0 + y1) / 2
        f = l["spans"][0]
        l["bold_start"] = bool(f["flags"] & 16) or bool(re.search(r"(bold|semibold|medium|-md)", f["font"].lower()))
        l["all_bold"] = all(bool(s["flags"] & 16) or bool(re.search(r"(bold|semibold|medium|-md)", s["font"].lower()))
                            for s in l["spans"] if s["text"].strip())
    return merged


# --------------------------------------------------------------------------
# BDA element model
# --------------------------------------------------------------------------

def load_elements(bda, page_sizes):
    pages = {}
    for el in bda.get("elements", []):
        locs = el.get("locations") or []
        if not locs:
            continue
        for li, loc in enumerate(locs):
            pi = loc["page_index"]
            if pi not in page_sizes:
                continue
            W, H = page_sizes[pi]
            bb = loc["bounding_box"]
            rect = (bb["left"] * W, bb["top"] * H, (bb["left"] + bb["width"]) * W, (bb["top"] + bb["height"]) * H)
            pages.setdefault(pi, []).append({
                "id": el["id"] if li == 0 else f'{el["id"]}-{li}',
                "src_id": el["id"],
                "type": el.get("type"),
                "sub": el.get("sub_type") or "",
                "order": el.get("reading_order", 0) + li * 0.001,
                "rect": rect,
                "html": (el.get("representation") or {}).get("html", ""),
                "title": el.get("title") or "",
                "lines": [],
            })
    for pi in pages:
        pages[pi].sort(key=lambda e: e["order"])
    return pages


def is_prose_table(el):
    soup = BeautifulSoup(el["html"], "html.parser")
    cells = soup.find_all(["td", "th"])
    texts = [c.get_text(" ", strip=True) for c in cells if c.get_text(strip=True)]
    if not texts:
        return True
    avg = sum(len(t) for t in texts) / len(texts)
    return avg > 80


def merge_figures(page_els):
    """BDA often splits one figure into stacked fragments; rejoin them."""
    figs = [e for e in page_els if e["type"] == "FIGURE" and e["sub"] not in ("ICON", "LOGO")]
    figs.sort(key=lambda e: e["rect"][1])
    keep = []
    for f in figs:
        if keep:
            k = keep[-1]
            a, b = k["rect"], f["rect"]
            h_overlap = min(a[2], b[2]) - max(a[0], b[0])
            if h_overlap > 0.8 * min(a[2] - a[0], b[2] - b[0]) and b[1] - a[3] < 15:
                k["rect"] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                k["merged"] = k.get("merged", [k["src_id"]]) + [f["src_id"]]
                f["type"] = "MERGED"
                continue
        keep.append(f)
    return [e for e in page_els if e["type"] != "MERGED"]


def classify(page_els, W, H, pi):
    """Decide a role for each element; drop spurious ones."""
    page_els = merge_figures(page_els)
    figs = [e for e in page_els if e["type"] == "FIGURE"]
    for e in page_els:
        e["role"] = None
        if e["type"] == "FIGURE":
            e["role"] = "figure"
        elif e["type"] == "TABLE":
            # tables that are really labels inside a figure
            ar = rect_area(e["rect"]) or 1
            if any(rect_area(intersect(e["rect"], f["rect"])) / ar > 0.5 for f in figs):
                e["role"] = "drop"
            else:
                e["role"] = "prose" if is_prose_table(e) else "table"
        else:
            sub = e["sub"]
            if sub in ("HEADER", "FOOTER", "PAGE_NUMBER") and pi > 0:
                e["role"] = "artifact"
            elif sub == "TITLE":
                e["role"] = "title"
            elif sub == "SECTION_HEADER":
                e["role"] = "section"
            elif sub == "LIST":
                soup = BeautifulSoup(e["html"], "html.parser")
                lis = soup.find_all("li")
                empty = sum(1 for li in lis if not li.get_text(strip=True))
                e["role"] = "drop" if lis and empty > len(lis) / 2 else "list"
            else:
                e["role"] = "para"
    return [e for e in page_els if e["role"] != "drop"]


def assign_lines(lines, els):
    orphans = []
    for l in lines:
        hit = [e for e in els if contains(e["rect"], l["cx"], l["cy"])]
        if not hit:
            orphans.append(l)
            continue
        figs = [e for e in hit if e["role"] == "figure"]
        owner = figs[0] if figs else min(hit, key=lambda e: rect_area(e["rect"]))
        owner["lines"].append(l)
    return orphans


def group_orphans(orphans, H, els=()):
    blocks = []
    for l in sorted(orphans, key=lambda l: (l["bbox"][1], l["bbox"][0])):
        placed = False
        for b in blocks:
            last = b[-1]
            if (abs(l["bbox"][0] - b[0]["bbox"][0]) < 25
                    and -l["size"] * 0.5 <= l["bbox"][1] - last["bbox"][3] < l["size"] * 1.2):
                b.append(l)
                placed = True
                break
        if not placed:
            blocks.append([l])
    out = []
    for b in blocks:
        x0 = min(l["bbox"][0] for l in b); y0 = min(l["bbox"][1] for l in b)
        x1 = max(l["bbox"][2] for l in b); y1 = max(l["bbox"][3] for l in b)
        margin = y1 < H * 0.06 or y0 > H * 0.94 or any(
            e["role"] == "artifact" and rect_area(intersect(e["rect"], (x0, y0, x1, y1))) > 0 for e in els)
        out.append({"id": f"orphan-{id(b)}", "type": "TEXT", "sub": "ORPHAN",
                    "role": "artifact" if margin else "para", "rect": (x0, y0, x1, y1),
                    "lines": b, "html": "", "title": "", "order": None})
    return out


def insert_orphans(els, orphan_els):
    for o in orphan_els:
        idx = 0
        for i, e in enumerate(els):
            if e["rect"][1] <= o["rect"][1] and not (e["rect"][2] < o["rect"][0] or o["rect"][2] < e["rect"][0]):
                idx = i + 1
        els.insert(idx, o)
    return els


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

class Ctx:
    page = 0
    mode = "screen"      # "screen" (browser/axe) or "print" (Prince tagged PDF)
    no_links = False     # print: running heads/feet are Artifacts, so no link annotations
    fonts = None         # PrintFonts in print mode

    def __init__(self, links, words):
        self.links = links      # list of (rect, uri)
        self.words = words      # set of lowercase words in the document
        self.n = 0
        self.link_ids = {}
        self.last_h = 0

    def heading(self, lvl):
        """Never skip a level going down (axe heading-order, WCAG 1.3.1)."""
        if self.last_h and lvl > self.last_h + 1:
            lvl = self.last_h + 1
        self.last_h = lvl
        return lvl

    def link_id(self, uri):
        # one id per (page, uri) pairing so visual twin and text link match
        key = (self.page, uri)
        if key not in self.link_ids:
            self.link_ids[key] = len(self.link_ids) + 1
        return self.link_ids[key]

    def uid(self, p="e"):
        self.n += 1
        return f"{p}{self.n}"


def link_for(span, links):
    x = (span["bbox"][0] + span["bbox"][2]) / 2
    y = (span["bbox"][1] + span["bbox"][3]) / 2
    for r, uri in links:
        if contains(r, x, y, tol=1):
            return uri
    return None


def soft_hyphen(prev_text, next_text, words):
    m1 = re.search(r"([A-Za-z]+)-$", prev_text.rstrip())
    m2 = re.match(r"\s*([a-z]+)", next_text)
    if not (m1 and m2):
        return False
    joined = (m1.group(1) + m2.group(1)).lower()
    hyph = (m1.group(1) + "-" + m2.group(1)).lower()
    if hyph in words and joined not in words:
        return False
    return True


def span_is_bold(s):
    return bool(s["flags"] & 16) or bool(re.search(r"(bold|semibold|black|heavy)", s["font"].lower()))


def span_is_italic(s):
    return bool(s["flags"] & 2) or bool(re.search(r"(italic|oblique|-it\b|it$)", s["font"].lower()))


def render_line(l, ctx, soft=False, space=False):
    """One absolutely positioned visual line (positions in pt).
    Screen mode: purely visual, inside an aria-hidden container; links stay
    mouse-clickable. Print mode: this IS the tagged text, so the line-end
    hyphen of a broken word becomes an Artifact, a real word space ends the
    line, and the line's width scaling is computed here from font metrics."""
    x0, y0, x1, y1 = l["bbox"]
    size = l["size"]
    top = l["baseline"] - size * 0.86
    pr = ctx.mode == "print"
    parts = []
    cur_link, buf = None, []

    def flush():
        nonlocal buf
        if not buf:
            return
        inner = "".join(buf)
        if cur_link and pr:
            parts.append(f'<a href="{html.escape(cur_link)}">{inner}</a>')
        elif cur_link:
            parts.append(f'<a href="{html.escape(cur_link)}" tabindex="-1" data-l="{ctx.link_id(cur_link)}">{inner}</a>')
        else:
            parts.append(inner)
        buf = []

    vis_spans = [s for s in l["spans"] if not s.get("invisible")]
    measured = 0.0
    for idx, s in enumerate(vis_spans):
        last = idx == len(vis_spans) - 1
        text = s["text"].rstrip() if (pr and last) else s["text"]
        hy = ""
        if pr and last and soft and text.endswith("-"):
            text, hy = text[:-1], '<span class="hy">-</span>'
        if pr:
            key, bold, italic = font_key(s["font"], s["flags"])
            span_pt = s["size"] if abs(s["size"] - size) > 0.3 else size
            measured += ctx.fonts.width(text + ("-" if hy else ""), key, bold, italic, span_pt)
            if last and space:
                ctx.fonts.width(" ", key, bold, italic, span_pt)
            fam = f"'{PRINT_FAMILIES[key]}'"
            wt, sty = (700 if bold else 400), ("italic" if italic else "normal")
        else:
            fam, wt, sty = font_css(s["font"], s["flags"])
        st = [f"font-family:{fam}"]
        if wt != 400:
            st.append(f"font-weight:{wt}")
        if sty != "normal":
            st.append("font-style:italic")
        if abs(s["size"] - size) > 0.3:
            st.append(f"font-size:{s['size'] / size:.3f}em")
        dy = s["origin"][1] - l["baseline"]
        if abs(dy) > 0.5:
            st.append(f"position:relative;top:{dy / size:.3f}em")
        col = s.get("color_out", s["color"] & 0xFFFFFF or DEFAULT_INK)
        if col != DEFAULT_INK:
            st.append(f"color:{color_hex(col)}")
        if l.get("bg") is not None and not pr:
            # opaque ground on the text itself: lets checkers resolve contrast
            st.append(f"background-color:{color_hex(l['bg'])}")
        uri = None if (pr and ctx.no_links) else link_for(s, ctx.links)
        if uri != cur_link:
            flush()
            cur_link = uri
        tail = " " if (pr and last and space) else ""
        buf.append(f'<span style="{";".join(st)}">{esc(text)}{hy}{tail}</span>')
    flush()
    xf = ""
    if pr and measured > 0:
        k = (x1 - x0) / measured
        if abs(k - 1) > 0.002:
            xf = f";transform:scaleX({min(max(k, 0.4), 1.8):.4f})"
    return (f'<span class="ln" data-w="{x1 - x0:.2f}" '
            f'style="left:{x0:.2f}pt;top:{top:.2f}pt;font-size:{size:.2f}pt{xf}">{"".join(parts)}</span>')


def line_joins(lines, ctx):
    """For each line: (soft, space) - soft = its final hyphen is a line-break
    hyphen to drop when reading; space = a word space follows the line."""
    out = []
    for i, l in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        soft = bool(nxt) and soft_hyphen(l["text"], nxt["text"], ctx.words)
        hard = l["text"].rstrip().endswith("-") and bool(nxt) and re.match(r"\s*[a-z]", nxt["text"])
        dropcap = len(l["text"].strip()) == 1 and bool(nxt) and l["size"] > 1.8 * nxt["size"]
        out.append((soft, bool(nxt) and not (soft or hard or dropcap)))
    return out


def clean_text(lines, ctx):
    """Reading text for assistive tech and reflow: lines joined, end-of-line
    hyphenation undone, superscripts, emphasis and links kept."""
    segs = []  # (text, fmt, uri)
    for i, l in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        soft = bool(nxt) and soft_hyphen(l["text"], nxt["text"], ctx.words)
        spans = l["spans"]
        for j, s in enumerate(spans):
            if s.get("invisible"):
                continue  # invisible in the original: not read aloud either
            t = s["text"]
            if j == len(spans) - 1:
                t = t.rstrip()
                if soft and t.endswith("-"):
                    t = t[:-1]
            dy = s["origin"][1] - l["baseline"]
            sup = bool(s["flags"] & 1) or (dy < -0.5 and s["size"] < l["size"] - 0.3)
            sub = dy > 0.5 and s["size"] < l["size"] - 0.3
            fmt = ("sup" if sup else "sub" if sub else "",
                   "b" if span_is_bold(s) else "", "i" if span_is_italic(s) else "")
            segs.append((t, fmt, link_for(s, ctx.links), ""))
        hard = l["text"].rstrip().endswith("-") and bool(nxt) and re.match(r"\s*[a-z]", nxt["text"])
        dropcap = len(l["text"].strip()) == 1 and bool(nxt) and l["size"] > 1.8 * nxt["size"]
        if nxt and not (soft or hard or dropcap):
            segs.append((" ", ("", "", ""), None, "space"))
    # merge into HTML, grouping consecutive segments that share a link
    out, i = [], 0
    while i < len(segs):
        uri = segs[i][2]
        j = i
        group = []
        while j < len(segs) and (segs[j][2] == uri or (segs[j][3] == "space" and j + 1 < len(segs) and uri and segs[j + 1][2] == uri)):
            group.append(segs[j])
            j += 1
        inner = []
        for t, fmt, _, _ in group:
            h = esc(t)
            if fmt[2] and t.strip():
                h = f"<i>{h}</i>"
            if fmt[1] and t.strip():
                h = f'<b>{h}</b>'
            if fmt[0] and t.strip():
                h = f"<{fmt[0]}>{h}</{fmt[0]}>"
            inner.append(h)
        inner = "".join(inner)
        out.append(f'<a href="{html.escape(uri)}" data-l="{ctx.link_id(uri)}">{inner}</a>' if uri else inner)
        i = j
    return re.sub(r"\s{2,}", " ", "".join(out)).strip()


def render_lines(lines, ctx, hidden=False):
    if ctx.mode == "print":
        joins = line_joins(lines, ctx)
        return '<span class="vis">' + "".join(
            render_line(l, ctx, soft, space) for l, (soft, space) in zip(lines, joins)) + "</span>"
    vis = "".join(render_line(l, ctx) + " " for l in lines)
    vis_html = f'<span class="vis" aria-hidden="true">{vis}</span>'
    if hidden:
        return vis_html
    return vis_html + f'<span class="txt">{clean_text(lines, ctx)}</span>'


def column_major(lines):
    cols = []
    for l in sorted(lines, key=lambda l: l["bbox"][0]):
        for c in cols:
            if abs(l["bbox"][0] - c["x"]) < 40:
                c["lines"].append(l)
                break
        else:
            cols.append({"x": l["bbox"][0], "lines": [l]})
    cols.sort(key=lambda c: c["x"])
    ordered = []
    for c in cols:
        ordered.extend(sorted(c["lines"], key=lambda l: l["bbox"][1]))
    return ordered


def split_paragraphs(lines):
    """Split prose lines into paragraphs at bold-led lines or big gaps."""
    paras, cur = [], []
    for i, l in enumerate(lines):
        prev = lines[i - 1] if i else None
        new = False
        if prev is not None:
            if (l["bbox"][0] - prev["bbox"][0] > 40 and l["bbox"][0] > prev["bbox"][2] - 20) or l["bbox"][1] < prev["bbox"][1] - 2:
                new = True  # column change
            elif l["bbox"][1] - prev["bbox"][3] > l["size"] * 0.7:
                new = True
            elif l["bold_start"] and not prev["all_bold"]:
                new = True
            elif prev["all_bold"] and prev["text"].strip().isupper() and len(prev["text"].strip()) < 60:
                new = True  # caps heading line followed by body
        if new and cur:
            paras.append(cur)
            cur = []
        cur.append(l)
    if cur:
        paras.append(cur)
    return paras


def heading_like(lines):
    t = " ".join(l["text"].strip() for l in lines)
    return (len(lines) <= 2 and len(t) <= 120 and all(l["all_bold"] for l in lines)
            and not re.match(r"(table|figure|fig\.)\s*\d", t, re.I) and not re.match(r"\d+\.", t))


_FIG_CACHE = {}


def render_figure(el, page, ctx, alt_map, dpi, report, pi):
    r = fitz.Rect(el["rect"]) & page.rect
    key = (pi, el["id"])
    if key not in _FIG_CACHE:
        pix = page.get_pixmap(clip=r, dpi=dpi)
        data = base64.b64encode(pix.tobytes("jpeg", jpg_quality=82)).decode()
        alt, weak = choose_alt(el, alt_map)
        report["figures"].append({"page": pi + 1, "id": el["src_id"], "sub_type": el["sub"], "alt": alt,
                                  "needs_review": weak})
        _FIG_CACHE[key] = (data, alt)
    data, alt = _FIG_CACHE[key]
    x0, y0, x1, y1 = r
    alt_attr = html.escape(alt, quote=True)
    tag = "figure" if alt else "div"
    wpx, hpx = round((x1 - x0) * PT2PX), round((y1 - y0) * PT2PX)
    return (f'<{tag} class="fig" style="left:{x0:.2f}pt;top:{y0:.2f}pt;width:{x1 - x0:.2f}pt;height:{y1 - y0:.2f}pt">'
            f'<img src="data:image/jpeg;base64,{data}" alt="{alt_attr}" width="{wpx}" height="{hpx}"></{tag}>')


def choose_alt(el, alt_map):
    sub = el["sub"]
    if sub == "ICON":
        return "", False
    soup = BeautifulSoup(el["html"], "html.parser")
    img = soup.find("img")
    key = (img.get("src") or "").split("/")[-1] if img else ""
    rem = alt_map.get(key, "")
    generic = {"LOGO", "ICON", "DIAGRAM", "IMAGE", "PHOTO", "CHART", "FIGURE", ""}
    weak_rem = rem.upper() in generic or re.match(r"(image of|diagram showing|picture of)\b", rem, re.I)
    if rem and not weak_rem:
        return rem, False
    p = soup.find("p")
    ptext = p.get_text(" ", strip=True) if p else ""
    pfirst = next((t.strip() for t in (p.get_text("\n") if p else "").split("\n") if t.strip()), "")
    if sub == "LOGO" and ptext:
        return re.sub(r"\s+", " ", ptext) + " logo", False
    title = pfirst if re.match(r"(figure|fig\.)\s*\d", pfirst, re.I) else (el["title"].split("\n")[0].strip() or pfirst)
    if title:
        return title, True  # a caption is not a description: flag for review
    return rem or "Figure", True


def _tnorm(t):
    t = re.sub(r"[\u2212\u2010\u2011\u2012\u2013\u2014]", "-", (t or "").lower())
    return re.sub(r"[^0-9a-z%+.-]", "", t)


def match_table_lines(table, lines):
    """Assign the table's visible PDF lines to BDA's cells (BDA gives cell
    text but no cell positions). Cells are walked in row-major order against
    the lines in row-major visual order; a cell may take several stacked lines."""
    cells = table.find_all(["td", "th"])
    pool = sorted(lines, key=lambda l: (round(l["baseline"]), l["bbox"][0]))
    used = set()
    assign = {}
    unmatched = []
    for ci, cell in enumerate(cells):
        tgt = _tnorm(cell.get_text(""))
        if not tgt:
            continue
        free = [i for i in range(len(pool)) if i not in used][:40]
        found = None
        for i in free:
            n = _tnorm(pool[i]["text"])
            if not n or not tgt.startswith(n):
                continue
            acc, got = [i], n
            while got != tgt:
                a0 = pool[acc[0]]
                cont = [j for j in range(len(pool)) if j not in used and j not in acc
                        and 0 < pool[j]["bbox"][1] - pool[acc[-1]]["bbox"][1] < 2.4 * a0["size"]
                        and abs(pool[j]["bbox"][0] - a0["bbox"][0]) < 12
                        and _tnorm(pool[j]["text"]) and tgt.startswith(got + _tnorm(pool[j]["text"]))]
                if not cont:
                    break
                j = min(cont, key=lambda j: pool[j]["bbox"][1])
                acc.append(j)
                got += _tnorm(pool[j]["text"])
            if got == tgt:
                found = acc
                break
        if found is None:  # tolerate small OCR differences in BDA's cell text
            best = max(free, key=lambda i: difflib.SequenceMatcher(None, tgt, _tnorm(pool[i]["text"])).ratio(),
                       default=None)
            if best is not None and difflib.SequenceMatcher(None, tgt, _tnorm(pool[best]["text"])).ratio() >= 0.85:
                found = [best]
        if found is None:
            unmatched.append(cell.get_text(" ", strip=True))
            continue
        used.update(found)
        assign[ci] = [pool[i] for i in found]
    leftovers = [pool[i] for i in range(len(pool)) if i not in used]
    return cells, assign, leftovers, unmatched


def prep_table(el):
    soup = BeautifulSoup(el["html"], "html.parser")
    table = soup.find("table")
    rows = table.find_all("tr")
    for tr in rows:
        cells = tr.find_all(["td", "th"])
        if cells and all(c.name == "th" for c in cells):
            for c in cells:
                c["scope"] = "col"
    # header rows into thead
    head_rows = []
    for tr in rows:
        cells = tr.find_all(["td", "th"])
        if cells and any(c.name == "th" for c in cells) and all(c.name == "th" or not c.get_text(strip=True) for c in cells):
            head_rows.append(tr)
        else:
            break
    for tr in head_rows:
        for c in tr.find_all("th"):
            c["scope"] = "col"
    if head_rows:
        thead = soup.new_tag("thead")
        tbody = soup.new_tag("tbody")
        for tr in rows:
            (thead if tr in head_rows else tbody).append(tr.extract())
        table.append(thead)
        table.append(tbody)
    return soup, table


def render_table(el, ctx, caption_id, report=None, pi=0):
    soup, table = prep_table(el)
    if ctx.mode == "print":
        return render_table_print(el, ctx, soup, table, report, pi)
    table["class"] = "sem-table"
    if caption_id:
        table["aria-labelledby"] = caption_id
    visual = render_lines(sorted(el["lines"], key=lambda l: (l["bbox"][1], l["bbox"][0])), ctx, hidden=True)
    return f'<div class="tbl"><div class="tbl-visual" aria-hidden="true">{visual}</div>{str(table)}</div>'


def render_table_print(el, ctx, soup, table, report, pi):
    cells, assign, leftovers, unmatched = match_table_lines(table, el["lines"])
    for ci, cell in enumerate(cells):
        lines = assign.get(ci)
        if lines is None:
            continue
        cell.clear()
        cell.append(BeautifulSoup(render_lines(sorted(lines, key=lambda l: (l["bbox"][1], l["bbox"][0])), ctx),
                                  "html.parser"))
    for ci, cell in enumerate(cells):
        if ci not in assign and cell.get_text(strip=True):
            # BDA saw text we could not place: keep it readable, mark for review
            txt = cell.get_text(" ", strip=True)
            cell.clear()
            span = soup.new_tag("span", attrs={"class": "unplaced"})
            span.string = txt
            cell.append(span)
    table["class"] = "ptbl"
    if report is not None:
        report["tables"].append({"page": pi + 1, "cells": len(cells),
                                 "cells_placed": len(assign),
                                 "cells_unplaced": unmatched,
                                 "lines_outside_cells": [l["text"].strip()[:60] for l in leftovers]})
    after = ""
    if leftovers:
        rest = sorted(leftovers, key=lambda l: (l["bbox"][1], l["bbox"][0]))
        after = "".join(f"<p>{render_lines(g, ctx)}</p>" for g in split_paragraphs(rest))
    return str(table) + after


def annotate_contrast(els, pix, scale, report, pi):
    """Give every visible text line an opaque background equal to the page
    colour behind it (so contrast is machine-checkable), and nudge any text
    colour that falls below WCAG 1.4.3 against that background."""
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    stats = report["contrast"]
    for e in els:
        if e["role"] in ("figure", "drop"):
            continue  # figure text is part of the figure image
        for l in e["lines"]:
            stats["lines"] += 1
            y0 = min(l["baseline"] - l["size"] * 0.92, l["bbox"][1])
            y1 = max(l["baseline"] + l["size"] * 0.24, l["bbox"][3])
            X0 = max(0, int(l["bbox"][0] * scale)); X1 = min(pix.w, int(np.ceil(l["bbox"][2] * scale)))
            Y0 = max(0, int(y0 * scale)); Y1 = min(pix.h, int(np.ceil(y1 * scale)))
            region = arr[Y0:Y1, X0:X1].reshape(-1, 3)
            if not len(region):
                continue
            spread = region.max(axis=0).astype(int) - region.min(axis=0).astype(int)
            uniform = int(spread.max()) <= 24
            if uniform:
                bg = tuple(int(v) for v in np.median(region, axis=0))
                l["bg"] = to_int(bg)
            else:
                # mixed ground (a rule or shading edge crosses the line): judge
                # against the dominant colour, leave transparent, flag for review
                q = (region // 8).astype(np.int32)
                keys, counts = np.unique(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2], return_counts=True)
                k = keys[counts.argmax()]
                mask = (q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]) == k
                bg = tuple(int(v) for v in np.median(region[mask], axis=0))
                stats["mixed_background"].append({"page": pi + 1, "text": l["text"].strip()[:60],
                                                   "dominant": color_hex(to_int(bg))})
            grounds = [bg]
            for s in l["spans"]:
                if not s["text"].strip():
                    continue
                fg = rgb(s["color"] & 0xFFFFFF or DEFAULT_INK)
                bold = font_css(s["font"], s["flags"])[1] >= 700
                need = required_ratio(s["size"], bold)
                worst = min(grounds, key=lambda g: contrast(fg, g))
                before = contrast(fg, worst)
                if before < 1.25:
                    # invisible in the original (e.g. white-on-white): leave it out
                    s["invisible"] = True
                    stats["invisible"].append({"page": pi + 1, "text": s["text"].strip()[:60]})
                    continue
                new = fix_color(fg, worst, need)
                s["color_out"] = to_int(new)
                if new != fg:
                    key = (color_hex(to_int(fg)), color_hex(to_int(new)), color_hex(to_int(worst)))
                    rec = stats["adjusted"].setdefault(key, {
                        "from": key[0], "to": key[1], "background": key[2],
                        "ratio_before": round(before, 2), "ratio_after": round(contrast(new, worst), 2),
                        "required": need, "spans": 0, "pages": set(), "example": s["text"].strip()[:40]})
                    rec["spans"] += 1
                    rec["pages"].add(pi + 1)


def build(pdf_path, bda_path, out_path, remediated=None, report_path=None, dpi=144, bg_dpi=144,
          print_out=None, fonts_dir=None):
    doc = fitz.open(pdf_path)
    bda = json.load(open(bda_path))
    page_sizes = {i: (p.rect.width, p.rect.height) for i, p in enumerate(doc)}
    pages = load_elements(bda, page_sizes)

    alt_map = {}
    if remediated:
        rs = BeautifulSoup(open(remediated, encoding="utf-8").read(), "html.parser")
        for img in rs.find_all("img"):
            alt_map[(img.get("src") or "").split("/")[-1]] = img.get("alt", "")

    words = set()
    for p in doc:
        words.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]*[A-Za-z]", p.get_text()))

    # heading sizes: decide levels from the PDF's own type sizes
    report = {"figures": [], "warnings": [], "tables": [],
              "contrast": {"lines": 0, "mixed_background": [], "invisible": [], "adjusted": {}}}
    per_page = {}
    # An ILL cover sheet (first page, no printed page number, followed by
    # numbered pages) gets its own heading scale so it can't skew the article.
    def has_folio(els):
        return any(e["sub"] == "PAGE_NUMBER" for e in els)

    def style_key(e):
        l = max(e["lines"], key=lambda l: l["size"])
        colored = any((s["color"] & 0xFFFFFF) not in (0, 0x231F20, 0x1A1A1A) for s in l["spans"])
        return (round(l["size"]), colored)

    for pi, page in enumerate(doc):
        W, H = page_sizes[pi]
        els = classify(pages.get(pi, []), W, H, pi)
        lines = extract_lines(page)
        orphans = assign_lines(lines, els)
        els = insert_orphans(els, group_orphans(orphans, H, els))
        per_page[pi] = els
    cover = len(doc) > 1 and not has_folio(per_page[0]) and any(has_folio(per_page[i]) for i in per_page if i)
    sec_keys = set()
    for pi, els in per_page.items():
        if cover and pi == 0:
            continue
        for e in els:
            if e["lines"] and e["role"] == "section":
                sec_keys.add(style_key(e))
    # bigger first; at equal size a coloured head outranks a black one
    sec_levels = sorted(sec_keys, key=lambda k: (-k[0], not k[1]))[:4]
    max_sec = max((k[0] for k in sec_levels), default=10)

    def level_for(e, pi):
        k = style_key(e)
        if cover and pi == 0:
            return 1 if e["role"] == "title" else 2
        if e["role"] == "title" and k[0] >= max_sec * 1.4:
            return 1
        if k in sec_levels:
            return 2 + sec_levels.index(k)
        below = [x for x in sec_levels if (-x[0], not x[1]) < (-k[0], not k[1])]
        return min(2 + len(below), 5)

    doc_title = ""
    best = 0
    for pi, els in per_page.items():
        for e in els:
            if e["role"] == "title" and e["lines"]:
                sz = max(l["size"] for l in e["lines"])
                if sz > best and pi > 0:
                    best, doc_title = sz, " ".join(l["text"].strip() for l in e["lines"])
    doc_title = doc_title or doc.metadata.get("title") or "Document"

    modes = ["screen"] + (["print"] if print_out else [])
    ctxs = {}
    for m in modes:
        c = Ctx([], words)
        c.mode = m
        if m == "print":
            c.fonts = PrintFonts(fonts_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"))
        ctxs[m] = c
    bodies = {m: [] for m in modes}
    _FIG_CACHE.clear()
    for pi, page in enumerate(doc):
        W, H = page_sizes[pi]
        els = per_page[pi]
        page_links = [(tuple(l["from"]), l["uri"]) for l in page.get_links() if l.get("uri")]

        # page label from the printed page number
        label = None
        for e in els:
            if e["sub"] == "PAGE_NUMBER" and e["lines"]:
                label = " ".join(l["text"].strip() for l in e["lines"])
        label = label or ("Cover sheet" if pi == 0 else f"Sheet {pi + 1}")
        pid = "page-" + re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower()
        page_name = f"Page {label}" if re.match(r"^[0-9ivxlcdmIVXLCDM]+$", label) else label

        # background: the page with its text removed, rasterized
        bgdoc = fitz.open()
        bgdoc.insert_pdf(doc, from_page=pi, to_page=pi)
        bp = bgdoc[0]
        # one page-sized redaction removes all text; images and vector art are kept
        bp.add_redact_annot(bp.rect)
        bp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                            text=fitz.PDF_REDACT_TEXT_REMOVE)
        bgpix = bp.get_pixmap(dpi=bg_dpi)
        annotate_contrast(els, bgpix, bg_dpi / 72, report, pi)
        bg = base64.b64encode(bgpix.tobytes("jpeg", jpg_quality=70)).decode()

        for mode in modes:
            ctx = ctxs[mode]
            ctx.links, ctx.page = page_links, pi
            bodies[mode].append(render_page(ctx, mode, page, pi, els, W, H, pid, page_name, bg,
                                            level_for, alt_map, dpi, report))

    page_w = max(w for w, h in page_sizes.values())
    out = TEMPLATE.replace("{{TITLE}}", esc(doc_title)) \
                  .replace("{{CSS}}", build_css(page_w)) \
                  .replace("{{BODY}}", "\n".join(bodies["screen"]))
    open(out_path, "w", encoding="utf-8").write(out)
    if print_out:
        sizes = sorted({(round(w, 2), round(h, 2)) for w, h in page_sizes.values()})
        pout = PRINT_TEMPLATE.replace("{{TITLE}}", esc(doc_title)) \
                             .replace("{{CSS}}", build_print_css(sizes, ctxs["print"].fonts)) \
                             .replace("{{BODY}}", "\n".join(bodies["print"]))
        open(print_out, "w", encoding="utf-8").write(pout)
    adj = []
    for rec in report["contrast"]["adjusted"].values():
        rec["pages"] = sorted(rec["pages"])
        adj.append(rec)
    report["contrast"]["adjusted"] = sorted(adj, key=lambda r: -r["spans"])
    if report_path:
        json.dump(report, open(report_path, "w"), indent=2)
    return report


def render_page(ctx, mode, page, pi, els, W, H, pid, page_name, bg, level_for, alt_map, dpi, report):
        pr = mode == "print"
        if pr:
            parts = [f'<div class="page" data-sz="{round(W, 2)}x{round(H, 2)}" style="width:{W:.2f}pt;height:{H:.2f}pt">',
                     f'<img class="bg" src="data:image/jpeg;base64,{bg}" alt="">']
        else:
            parts = [f'<div class="page" style="width:{W:.2f}pt;height:{H:.2f}pt">',
                     f'<hr class="pb" role="doc-pagebreak" id="{pid}" aria-label="{html.escape(page_name)}">'
                     f'<span class="pbl" aria-hidden="true">{html.escape(page_name)}</span>',
                     f'<img class="bg" src="data:image/jpeg;base64,{bg}" alt="" aria-hidden="true">']

        prev_caption_id = None
        for e in els:
            role = e["role"]
            if role == "figure":
                parts.append(render_figure(e, page, ctx, alt_map, dpi, report, pi))
                prev_caption_id = None
                continue
            if not e["lines"]:
                continue
            if role == "table":
                parts.append(render_table(e, ctx, prev_caption_id, report if pr else None, pi))
                prev_caption_id = None
                continue
            lines = sorted(e["lines"], key=lambda l: (l["bbox"][1], l["bbox"][0]))
            if role == "artifact":
                aria = "" if pr else ' aria-hidden="true"'
                ctx.no_links = pr
                parts.append(f'<div class="artifact"{aria}>{render_lines(lines, ctx, hidden=True)}</div>')
                ctx.no_links = False
                continue
            if role in ("title", "section"):
                lvl = ctx.heading(level_for(e, pi))
                hid = ctx.uid("h")
                parts.append(f'<h{lvl} id="{hid}">{render_lines(lines, ctx)}</h{lvl}>')
                prev_caption_id = None
                continue
            if role == "prose":
                lines = column_major(e["lines"])
            groups = split_paragraphs(lines) if role in ("prose", "para", "list") else [lines]
            if role == "list":
                parts.append('<ul class="lst">')
                for g in groups:
                    inner = render_lines(g, ctx)
                    parts.append(f'<li><span class="lb">{inner}</span></li>' if pr else f'<li>{inner}</li>')
                parts.append('</ul>')
                continue
            for g in groups:
                text = " ".join(l["text"].strip() for l in g)
                if heading_like(g) and e["sub"] != "ORPHAN" and (role == "prose" or len(groups) == 1) and len(text) > 2:
                    hid = ctx.uid("h")
                    lvl = ctx.heading(level_for({"lines": g, "role": "section"}, pi))
                    parts.append(f'<h{lvl} id="{hid}">{render_lines(g, ctx)}</h{lvl}>')
                    prev_caption_id = None
                    continue
                pid2 = ctx.uid("p")
                parts.append(f'<p id="{pid2}">{render_lines(g, ctx)}</p>')
                prev_caption_id = pid2 if re.match(r"table\s*\d", text, re.I) else None
        parts.append("</div>")
        return "".join(parts)


# --------------------------------------------------------------------------
# page / reflow CSS (reflow rules emitted twice: media query + toggle class)
# --------------------------------------------------------------------------

REFLOW_RULES = [
    (".page", "width:auto!important;height:auto!important;margin:0 auto 2rem;padding:0;box-shadow:none;background:none;max-width:42rem"),
    (".page > .bg, .artifact, .vis, .tbl-visual", "display:none!important"),
    (".page .txt", "position:static;width:auto;height:auto;overflow:visible;clip:auto;clip-path:none;white-space:normal"),
    (".page .txt a", "color:var(--link)"),
    (".fig", "position:static;width:auto!important;height:auto!important;margin:1.5rem 0"),
    ("div.fig", "display:none"),
    (".fig img", "width:auto!important;max-width:100%;height:auto!important"),
    (".sem-table", "position:static;width:100%;height:auto;clip:auto;clip-path:none;overflow:visible;margin:1rem 0;border-collapse:collapse;font-size:.9rem"),
    (".sem-table th, .sem-table td", "border:1px solid var(--rule);padding:.3rem .5rem;text-align:left;vertical-align:top"),
    (".pb", "display:block;position:static;width:auto;height:0;border:0;border-top:1px dashed var(--rule);margin:2rem 0 .3rem"),
    (".pbl", "display:block;margin:0 0 1rem;font:600 .75rem/1 system-ui,sans-serif;color:var(--muted)"),
    (".page p, .page li", "margin:0 0 .9rem"),
    (".page h1, .page h2, .page h3, .page h4, .page h5, .page h6", "margin:1.4rem 0 .6rem;line-height:1.25;font-weight:700"),
    (".page h1", "font-size:1.75rem"), (".page h2", "font-size:1.4rem"), (".page h3", "font-size:1.2rem"),
    (".page h4", "font-size:1.05rem"), (".page h5, .page h6", "font-size:1rem"),
]


def build_css(page_w):
    base = """
:root{--paper:#fff;--desk:#e9e9ea;--ink:#111;--rule:#9a9a9a;--muted:#555;--link:#0b57d0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--desk:#1f1f22}}
html{background:var(--desk)}
body{margin:0;padding:56px 16px 32px;color:var(--ink);font-family:Georgia,'Times New Roman',serif;line-height:1.5}
.page{position:relative;margin:0 auto 24px;background:var(--paper);box-shadow:0 1px 4px rgba(0,0,0,.25);overflow:hidden;color:#111}
.page > .bg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.page p,.page h1,.page h2,.page h3,.page h4,.page h5,.page h6,.page ul,.page li,.page figure{margin:0;padding:0;font-size:inherit;font-weight:inherit;list-style:none}
.ln{position:absolute;white-space:pre;line-height:1;transform-origin:0 0;color:#111}
.ln a{color:inherit;text-decoration:underline;text-decoration-thickness:.06em;text-underline-offset:.14em}
.ln a.kbd-focus{outline:2px solid #0b57d0;outline-offset:1px;border-radius:2px}
.page .txt{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
.fig{position:absolute;margin:0}
.fig img{display:block;width:100%;height:100%}
.sem-table{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
.pb{position:absolute;top:0;left:0;width:1px;height:0;margin:0;border:0}
.pbl{display:none}
.toolbar{position:fixed;top:0;left:0;right:0;z-index:5;display:flex;gap:8px;align-items:center;justify-content:center;padding:8px 16px;background:var(--desk);border-bottom:1px solid var(--rule);font:14px/1.2 system-ui,sans-serif}
.toolbar button{font:inherit;padding:6px 12px;border:1px solid var(--rule);border-radius:6px;background:var(--paper);color:#111;cursor:pointer}
.toolbar button:focus-visible{outline:2px solid #0b57d0;outline-offset:2px}
"""
    reflow = "\n".join(f"{sel}{{{decl}}}" for sel, decl in REFLOW_RULES)
    toggled = "\n".join(",".join(f"body.reflow {s.strip()}" for s in sel.split(",")) + f"{{{decl}}}"
                        for sel, decl in REFLOW_RULES)
    breakpoint = int(page_w * PT2PX + 48)
    return (base + f"\n@media (max-width:{breakpoint}px){{html,body{{background:var(--paper)}}body{{padding-top:8px}}.toolbar{{display:none}}\n{reflow}\n}}\n"
            + "html:has(body.reflow),body.reflow{background:var(--paper)}\n" + toggled + "\n")


# Tagging and the PDF/UA profile are set inside the document, so the file
# produces an accessible PDF even from the Prince desktop app, which has no
# option for them (prince-pdf-tagged needs Prince builds from late 2024 on).
PRINCE_PDF_SETTINGS = """@prince-pdf{
  prince-pdf-profile: "PDF/UA-1";
  prince-pdf-tagged: enable;
}
"""


def _tag(t):
    return f"-prince-pdf-tag-type:{t};prince-pdf-tag-type:{t}"


def build_print_css(sizes, fonts):
    pages = "\n".join(f"@page sz{w:g}x{h:g}{{size:{w:g}pt {h:g}pt;margin:0}}\n"
                      f".page[data-sz=\"{w:g}x{h:g}\"]{{page:sz{w:g}x{h:g}}}" for w, h in sizes)
    return PRINCE_PDF_SETTINGS + fonts.css() + "\n" + pages + f"""
@page{{margin:0}}
html,body{{margin:0;padding:0;background:#fff}}
.page{{position:relative;overflow:hidden;break-after:page;page-break-after:always;color:#111}}
.page:last-child{{break-after:auto;page-break-after:auto}}
.page > .bg{{position:absolute;left:0;top:0;width:100%;height:100%}}
.page p,.page h1,.page h2,.page h3,.page h4,.page h5,.page h6,.page ul,.page li,.page figure,.page table{{margin:0;padding:0;border:0;font-size:inherit;font-weight:inherit;list-style:none}}
.ln{{position:absolute;white-space:pre;line-height:1;transform-origin:0 0;font-kerning:none;font-variant-ligatures:none}}
.ln a{{color:inherit;text-decoration:underline;text-decoration-thickness:.06em;text-underline-offset:.14em}}
.fig{{position:absolute;margin:0}}
.fig img{{display:block;width:100%;height:100%}}
.ptbl{{position:absolute;left:0;top:0;border-collapse:collapse}}
.ptbl td,.ptbl th{{padding:0;border:0;font-weight:inherit;text-align:left}}
.unplaced{{position:absolute;left:0;top:0;font-size:1pt;color:#fff}}
/* ---- PDF tag mapping (PrinceXML 16+) ---- */
.page,.vis,.ln span,figure.fig{{{_tag("none")}}}
.ln{{{_tag("Span")}}}
.page > .bg,.artifact,.artifact *,div.fig,div.fig img,.ln span.hy{{{_tag("Artifact")}}}
ul.lst{{{_tag("L")}}}
ul.lst > li{{{_tag("LI")}}}
ul.lst .lb{{{_tag("LBody")}}}
figure.fig img{{{_tag("Figure")}}}
"""


PRINT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{TITLE}}</title>
<style>{{CSS}}</style>
</head>
<body>
{{BODY}}
</body>
</html>
"""


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>{{CSS}}</style>
</head>
<body>
<div class="toolbar" role="region" aria-label="View options">
<button type="button" id="viewToggle" aria-pressed="false">Reflow text</button>
</div>
<main id="main-content">
{{BODY}}
</main>
<script>
(function(){
  var PT=96/72, body=document.body, btn=document.getElementById('viewToggle');
  var mq=window.matchMedia('(max-width:'+Math.round(parseFloat(document.querySelector('.page').style.width)*PT+48)+'px)');
  function paged(){ return !body.classList.contains('reflow') && !mq.matches; }
  function fit(){
    var lines=document.querySelectorAll('.ln');
    if(!paged()){ lines.forEach(function(l){l.style.transform='';}); return; }
    lines.forEach(function(l){
      l.style.transform='';
      var w=l.getBoundingClientRect().width, t=parseFloat(l.dataset.w)*PT;
      if(w>0 && t>0) l.style.transform='scaleX('+(t/w).toFixed(4)+')';
    });
  }
  btn.addEventListener('click',function(){
    var on=body.classList.toggle('reflow');
    btn.setAttribute('aria-pressed', on?'true':'false');
    btn.textContent=on?'Page view':'Reflow text';
    fit();
  });
  mq.addEventListener('change',fit);
  document.addEventListener('focusin',function(e){
    var a=e.target.closest&&e.target.closest('.txt a'); if(!a||!paged()) return;
    var v=a.closest('.page').querySelector('.vis a[data-l="'+a.dataset.l+'"]');
    if(v){v.classList.add('kbd-focus'); v.scrollIntoView({block:'nearest'});}
  });
  document.addEventListener('focusout',function(e){
    document.querySelectorAll('.kbd-focus').forEach(function(v){v.classList.remove('kbd-focus');});
  });
  (document.fonts&&document.fonts.ready?document.fonts.ready:Promise.resolve()).then(fit);
  window.addEventListener('load',fit);
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("bda_json")
    ap.add_argument("-o", "--out", default="layout.html")
    ap.add_argument("--remediated", help="remediated.html from the pipeline (alt text source)")
    ap.add_argument("--report", help="write a JSON review report here")
    ap.add_argument("--dpi", type=int, default=144, help="figure image resolution")
    ap.add_argument("--bg-dpi", type=int, default=144, help="page background resolution")
    ap.add_argument("--print-out", help="also write a PrinceXML tagged-PDF source file here")
    ap.add_argument("--fonts", help="folder with Liberation *.ttf fonts (default: ./fonts next to this script)")
    a = ap.parse_args()
    rep = build(a.pdf, a.bda_json, a.out, a.remediated, a.report, a.dpi, a.bg_dpi, a.print_out, a.fonts)
    flagged = [f for f in rep["figures"] if f["needs_review"]]
    print(f"wrote {a.out}: {len(rep['figures'])} figures, {len(flagged)} need alt-text review")


if __name__ == "__main__":
    main()
