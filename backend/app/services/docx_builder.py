"""Génération du DOCX éditable à partir du modèle intermédiaire (PLAN.md §8).

Le builder ne dépend que de structures simples (`PageModel` / `BlockModel`) : testable sans base de données.
Structure éditable : styles Titre, paragraphes, listes, vrais tableaux Word, en-tête / pied de page réels si répétés.
Pas de zones de texte positionnées. Arabe : `w:bidi` sur le paragraphe, `w:rtl` sur les segments arabes,
police « complex script » définie, `w:bidiVisual` sur les tableaux majoritairement arabes.
"""
from __future__ import annotations

import html as htmlmod
import math
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from lxml import html as lxml_html

from app.services.ocr.html_utils import arabic_ratio, direction_of, to_text

VISUAL = {"Picture", "Figure", "Diagram", "Image", "Stamp"}
SIZE_SCALE = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24]
ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
LATIN_RE = re.compile(r"[A-Za-zÀ-ɏ]")


@dataclass
class BlockModel:
    label: str
    html: str
    bbox: tuple[float, float, float, float]
    direction: str = "ltr"
    crop_path: str | None = None  # chemin absolu d'une image


@dataclass
class PageModel:
    width: int
    height: int
    blocks: list[BlockModel] = field(default_factory=list)
    dpi: int = 200


@dataclass
class Options:
    arabic_font: str = "Arial"
    latin_font: str = "Calibri"


# ------------------------------------------------------------------ XML utilitaires

def _rpr(run):
    return run._r.get_or_add_rPr()


def _set_fonts(run, font: str, cs_font: str | None = None) -> None:
    rpr = _rpr(run)
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
        rf.set(qn(attr), font)
    rf.set(qn("w:cs"), cs_font or font)


def _mark_rtl(run, size_pt: float, bold: bool) -> None:
    rpr = _rpr(run)
    rpr.append(OxmlElement("w:rtl"))
    szcs = OxmlElement("w:szCs")
    szcs.set(qn("w:val"), str(int(size_pt * 2)))
    rpr.append(szcs)
    if bold:
        rpr.append(OxmlElement("w:bCs"))


def _bidi_paragraph(p) -> None:
    ppr = p._p.get_or_add_pPr()
    if ppr.find(qn("w:bidi")) is None:
        ppr.insert(0, OxmlElement("w:bidi"))


def _classify(ch: str) -> str:
    if ARABIC_RE.match(ch):
        return "ar"
    if LATIN_RE.match(ch):
        return "la"
    return "neutral"


def _split_by_script(text: str) -> list[tuple[str, str]]:
    """[(segment, 'ar'|'la')] : les neutres (espaces, chiffres, ponctuation) suivent le segment courant."""
    out: list[list[str]] = []  # [script, texte]
    for ch in text:
        k = _classify(ch)
        if k == "neutral":
            if out:
                out[-1][1] += ch
            else:
                out.append(["la", ch])
        elif out and out[-1][0] == k:
            out[-1][1] += ch
        else:
            out.append([k, ch])
    return [(t, k) for k, t in out]


# ------------------------------------------------------------------ analyse du HTML d'un bloc

@dataclass
class Seg:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    br: bool = False


def parse_inline(fragment: str) -> list[list[Seg]]:
    """Découpe un fragment HTML en paragraphes de segments (gras / italique / souligné / saut de ligne)."""
    fragment = fragment.strip()
    if not fragment:
        return []
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except Exception:  # noqa: BLE001
        return [[Seg(to_text(fragment))]]
    paras: list[list[Seg]] = [[]]

    def walk(node, bold=False, italic=False, underline=False):
        tag = node.tag if isinstance(node.tag, str) else ""
        b = bold or tag in ("b", "strong", "th")
        i = italic or tag in ("i", "em")
        u = underline or tag == "u"
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6") and node is not root and paras[-1]:
            paras.append([])
        if node.text:
            paras[-1].append(Seg(node.text, b, i, u))
        for child in node:
            ctag = child.tag if isinstance(child.tag, str) else ""
            if ctag == "br":
                paras[-1].append(Seg("", br=True))
            else:
                walk(child, b, i, u)
            if child.tail:
                paras[-1].append(Seg(child.tail, b, i, u))
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6") and node is not root:
            paras.append([])

    walk(root)
    cleaned = []
    for p in paras:
        for s in p:
            if not s.br:
                s.text = re.sub(r"\s+", " ", htmlmod.unescape(s.text))
        if any(s.br or s.text.strip() for s in p):
            cleaned.append(p)
    return cleaned


def heading_level(html: str) -> int | None:
    m = re.match(r"\s*<h([1-6])", html, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ constructeur

class DocxBuilder:
    def __init__(self, pages: list[PageModel], options: Options | None = None, repeated: dict[str, str] | None = None):
        self.pages = pages
        self.opt = options or Options()
        self.repeated = repeated or {}
        self.doc = DocxDocument()
        self.page_w_cm = 21.0
        self.page_h_cm = 29.7
        self.content_w_cm = 17.0
        self.body_pt = 11
        self._env_left = self._env_right = 0.0

    # --- mise en page
    def _setup_section(self) -> None:
        landscape = statistics.fmean([p.width / max(1, p.height) for p in self.pages]) > 1.05
        w, h = (29.7, 21.0) if landscape else (21.0, 29.7)
        sec = self.doc.sections[0]
        sec.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Cm(w), Cm(h)
        self.page_w_cm, self.page_h_cm = w, h
        lefts, rights, tops, bottoms = [], [], [], []
        for p in self.pages:
            bs = [b.bbox for b in p.blocks if b.label not in ("PageHeader", "PageFooter")] or [b.bbox for b in p.blocks]
            if not bs or not p.width or not p.height:
                continue
            lefts.append(min(b[0] for b in bs) / p.width)
            rights.append(1 - max(b[2] for b in bs) / p.width)
            tops.append(min(b[1] for b in bs) / p.height)
            bottoms.append(1 - max(b[3] for b in bs) / p.height)

        def cm(v: list[float], size: float, lo: float = 1.2, hi: float = 4.0) -> float:
            return min(hi, max(lo, (statistics.median(v) if v else 0.1) * size))

        sec.left_margin, sec.right_margin = Cm(cm(lefts, w)), Cm(cm(rights, w))
        sec.top_margin, sec.bottom_margin = Cm(cm(tops, h, 1.5, 3.5)), Cm(cm(bottoms, h, 1.5, 3.5))
        self.content_w_cm = w - sec.left_margin.cm - sec.right_margin.cm
        self._env_left = statistics.median(lefts) if lefts else 0.08
        self._env_right = 1 - (statistics.median(rights) if rights else 0.08)

    def _estimate_pt(self, b: BlockModel, page: PageModel) -> float | None:
        text = to_text(b.html)
        if len(text) < 3:
            return None
        w, h = b.bbox[2] - b.bbox[0], b.bbox[3] - b.bbox[1]
        if w <= 0 or h <= 0:
            return None
        px = min(math.sqrt(h * w / (0.6 * len(text))), h / 1.2)
        return px * 72 / (page.dpi or 200)

    @staticmethod
    def _snap(pt: float) -> int:
        return min(SIZE_SCALE, key=lambda s: abs(s - pt))

    def _body_size(self) -> int:
        est = [self._estimate_pt(b, p) for p in self.pages for b in p.blocks if b.label == "Text"]
        est = [e for e in est if e]
        return self._snap(statistics.median(est)) if est else 11

    def _alignment(self, b: BlockModel, page: PageModel):
        if not page.width:
            return None
        text = to_text(b.html)
        x0, x1 = b.bbox[0] / page.width, b.bbox[2] / page.width
        env_w = max(0.1, self._env_right - self._env_left)
        width = (x1 - x0) / env_w
        center_gap = abs((x0 + x1) / 2 - (self._env_left + self._env_right) / 2)
        if len(text) < 140 and width < 0.8 and center_gap < 0.05:
            return WD_ALIGN_PARAGRAPH.CENTER
        if b.direction == "ltr" and (self._env_right - x1) < 0.03 and (x0 - self._env_left) > 0.35 and len(text) < 140:
            return WD_ALIGN_PARAGRAPH.RIGHT
        return None  # début de ligne : gauche (LTR) ou droite (RTL, par w:bidi)

    # --- runs
    def _add_runs(self, p, segs: list[Seg], rtl_para: bool, size: float, bold_all: bool = False) -> None:
        for seg in segs:
            if seg.br:
                p.add_run().add_break(WD_BREAK.LINE)
                continue
            if not seg.text:
                continue
            for text, script in _split_by_script(seg.text):
                run = p.add_run(text)
                run.font.size = Pt(size)
                run.bold = seg.bold or bold_all or None
                run.italic = seg.italic or None
                run.underline = seg.underline or None
                if script == "ar":
                    _set_fonts(run, self.opt.arabic_font, self.opt.arabic_font)
                    _mark_rtl(run, size, bool(run.bold))
                else:
                    _set_fonts(run, self.opt.latin_font, self.opt.arabic_font)

    def _paragraph(self, container, segs, direction: str, size: float, style: str | None = None,
                   align=None, bold_all: bool = False):
        p = container.add_paragraph(style=style) if style else container.add_paragraph()
        if direction == "rtl":
            _bidi_paragraph(p)
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_after = Pt(4)
        self._add_runs(p, segs, direction == "rtl", size, bold_all)
        return p

    # --- blocs
    def _heading(self, b: BlockModel, page: PageModel, rank: dict[int, int]) -> None:
        paras = parse_inline(b.html) or [[Seg(to_text(b.html))]]
        level = heading_level(b.html) or rank.get(id(b), 2)
        level = min(max(level, 1), 3)
        size = {1: 18, 2: 14, 3: 12}[level]
        for segs in paras:
            self._paragraph(self.doc, segs, b.direction, size, style=f"Heading {level}", align=self._alignment(b, page))

    def _text(self, b: BlockModel, page: PageModel, style: str | None = None, size: float | None = None) -> None:
        paras = parse_inline(b.html)
        size = size or self.body_pt
        for segs in paras:
            self._paragraph(self.doc, segs, b.direction, size, style=style, align=self._alignment(b, page))

    def _picture(self, b: BlockModel, page: PageModel) -> None:
        if not b.crop_path or not Path(b.crop_path).is_file():
            return
        w_rel = (b.bbox[2] - b.bbox[0]) / max(1, page.width)
        width = max(1.5, min(self.content_w_cm, w_rel * self.page_w_cm))
        para = self.doc.add_paragraph()
        para.alignment = self._alignment(b, page)
        para.add_run().add_picture(b.crop_path, width=Cm(width))

    def _table(self, b: BlockModel, page: PageModel) -> None:
        try:
            root = lxml_html.fragment_fromstring(b.html, create_parent="div")
        except Exception:  # noqa: BLE001
            self._text(b, page)
            return
        rows = root.xpath(".//tr")
        if not rows:
            self._text(b, page)
            return
        grid: list[list[tuple | None]] = []
        ncols = 0
        for r, tr in enumerate(rows):
            while len(grid) <= r:
                grid.append([])
            c = 0
            for cell in tr.xpath("./td|./th"):
                while c < len(grid[r]) and grid[r][c] is not None:
                    c += 1
                rs = max(1, int(cell.get("rowspan", 1) or 1))
                cs = max(1, int(cell.get("colspan", 1) or 1))
                for dr in range(rs):
                    while len(grid) <= r + dr:
                        grid.append([])
                    row = grid[r + dr]
                    while len(row) < c + cs:
                        row.append(None)
                    for dc in range(cs):
                        row[c + dc] = ("span", r, c) if (dr or dc) else ("cell", cell, rs, cs)
                c += cs
            ncols = max(ncols, len(grid[r]))
        nrows = len(grid)
        if not ncols:
            return
        table = self.doc.add_table(rows=nrows, cols=ncols)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        rtl_cells = sum(1 for tr in rows for td in tr.xpath("./td|./th") if arabic_ratio(to_text(lxml_html.tostring(td, encoding="unicode"))) > 0.5)
        total_cells = sum(len(tr.xpath("./td|./th")) for tr in rows) or 1
        if rtl_cells / total_cells > 0.5:
            tblpr = table._tbl.tblPr
            tblpr.append(OxmlElement("w:bidiVisual"))
        for r in range(nrows):
            for c in range(ncols):
                entry = grid[r][c] if c < len(grid[r]) else None
                if entry is None or entry[0] != "cell":
                    continue
                _, cell, rs, cs = entry
                tc = table.cell(r, c)
                if rs > 1 or cs > 1:
                    tc = tc.merge(table.cell(min(nrows - 1, r + rs - 1), min(ncols - 1, c + cs - 1)))
                inner = "".join(lxml_html.tostring(ch, encoding="unicode") for ch in cell) if len(cell) else ""
                frag = (cell.text or "") + inner if len(cell) else (cell.text or "")
                segs_list = parse_inline(frag) or [[Seg("")]]
                cell_dir = direction_of(to_text(frag))
                first = True
                for segs in segs_list:
                    para = tc.paragraphs[0] if first else tc.add_paragraph()
                    first = False
                    if cell_dir == "rtl":
                        _bidi_paragraph(para)
                    self._add_runs(para, segs, cell_dir == "rtl", max(8, self.body_pt - 1), bold_all=cell.tag == "th")
        self.doc.add_paragraph()

    def _set_header_footer(self) -> None:
        sec = self.doc.sections[0]
        for label, part in (("PageHeader", sec.header), ("PageFooter", sec.footer)):
            html = self.repeated.get(label)
            if not html:
                continue
            para = part.paragraphs[0]
            segs_list = parse_inline(html) or [[Seg(to_text(html))]]
            direction = direction_of(to_text(html))
            for k, segs in enumerate(segs_list):
                target = para if k == 0 else part.add_paragraph()
                if direction == "rtl":
                    _bidi_paragraph(target)
                self._add_runs(target, segs, direction == "rtl", 9)

    # --- orchestration
    def build(self) -> DocxDocument:
        if not self.pages:
            return self.doc
        self._setup_section()
        self.body_pt = self._body_size()
        heights = sorted(b.bbox[3] - b.bbox[1] for p in self.pages for b in p.blocks if b.label == "SectionHeader")
        rank: dict[int, int] = {}
        for p in self.pages:
            for b in p.blocks:
                if b.label == "SectionHeader" and heights:
                    pos = heights.index(b.bbox[3] - b.bbox[1]) / max(1, len(heights) - 1)
                    rank[id(b)] = 1 if pos > 0.75 else (2 if pos > 0.35 else 3)
        self._set_header_footer()
        repeated_keys = {k: re.sub(r"\d+", "#", to_text(v).lower()).strip() for k, v in self.repeated.items()}
        for n, page in enumerate(self.pages):
            if n:
                self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            for b in page.blocks:
                key = re.sub(r"\d+", "#", to_text(b.html).lower()).strip()
                if b.label in ("PageHeader", "PageFooter") and repeated_keys.get(b.label) == key and key:
                    continue  # déjà dans le vrai en-tête / pied de page
                if b.label in VISUAL:
                    self._picture(b, page)
                elif b.label == "Table":
                    self._table(b, page)
                elif b.label == "SectionHeader":
                    self._heading(b, page, rank)
                elif b.label in ("ListGroup", "ListItem"):
                    self._text(b, page, style="List Bullet")
                elif b.label == "Caption":
                    self._text(b, page, style="Caption", size=max(8, self.body_pt - 2))
                elif b.label == "Footnote":
                    self._text(b, page, size=max(8, self.body_pt - 3))
                else:
                    self._text(b, page)
        return self.doc

    def save(self, path: str | Path) -> None:
        self.build().save(str(path))
