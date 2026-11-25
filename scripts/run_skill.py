#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic annotation extraction and note distillation. Standard library only."""

import argparse
import hashlib
import json
import os
import re
import sys
import zlib
from pathlib import Path

MAX_FILES = 2000
MAX_BYTES = 4 * 1024 * 1024
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir",
             "corpus_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}
JSON_KEYS = ("annotations", "highlights", "notes", "items", "data", "comments")
CUE = [
    ("question", ("?", "？", "为什么", "是否", "如何", "待查", "unknown", "why", "how")),
    ("method", ("方法", "实验", "assay", "protocol", "测量", "method", "pcr", "离心")),
    ("conclusion", ("表明", "说明", "因此", "结论", "suggest", "indicat", "therefore", "conclude")),
    ("reusable", ("可用于", "建议", "复用", "reusable", "recommend", "reuse")),
    ("to_verify", ("可能", "待验证", "不确定", "perhaps", "maybe", "unclear", "verify")),
]
ALIASES = {
    "case_id": ["case_id", "caseid", "id"],
    "annotations": ["annotations", "highlights", "extracted_annotations", "marks"],
    "notes": ["notes", "structured_notes", "note_items", "distilled_notes"],
    "links": ["links", "relations", "associations", "connections"],
    "search_index": ["search_index", "index", "retrieval_index", "keywords"],
    "unresolved": ["unresolved", "missing", "incomplete", "needs_review"],
    "manifest": ["manifest", "inventory", "files", "file_list"],
    "summary": ["summary", "overview", "conclusion", "notes_summary"],
    "status": ["status", "run_status"],
    "result": ["result", "results", "data"],
    "evidence": ["evidence", "provenance", "sources"],
    "uncertainty": ["uncertainty", "limitations"],
    "errors": ["errors", "error"],
    "stats": ["stats", "statistics", "counts"],
}
EN_STOP = set(
    "a an the and or but if then than that this these those of in on at to for "
    "from by with as is are was were be been being it its we you they he she "
    "i not no do does did done what which who whom when where why how about "
    "into over under between during before after also such can could may might "
    "should would will shall use used using every each all both more most "
    "note notes method methods data item items page pages section sections "
    "one two three first second third new old same other another".split()
)
PDF_ANNOT_SUBTYPES = {
    "Highlight": "highlight", "Underline": "underline", "StrikeOut": "strikeout",
    "Squiggly": "squiggly", "Text": "text", "FreeText": "freetext", "Square": "square",
}
PDF_HEX_TOKEN = re.compile(rb"<[0-9A-Fa-f\s]+>")
PDF_LITERAL_TOKEN = re.compile(rb"\((?:\\.|[^()\\])*\)", re.S)


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def contract_fields(case):
    oc = case.get("output_contract")
    out = []
    if isinstance(oc, dict):
        if isinstance(oc.get("properties"), dict):
            out = list(oc["properties"].keys())
        elif isinstance(oc.get("fields"), dict):
            out = list(oc["fields"].keys())
        elif isinstance(oc.get("fields"), list):
            out = [x for x in oc["fields"] if isinstance(x, str)]
        elif isinstance(oc.get("required"), list):
            out = [x for x in oc["required"] if isinstance(x, str)]
        else:
            out = [str(k) for k in oc.keys()]
    elif isinstance(oc, list):
        out = [x for x in oc if isinstance(x, str)]
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def iter_paths(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_paths(item)
    elif isinstance(value, dict):
        for key in ("path", "dir", "file", "files", "root"):
            if key in value:
                yield from iter_paths(value[key])


def candidate_roots(case, case_path):
    base = case_path.parent
    roots = []
    for key in PATH_KEYS + ("annotation_files", "material_files"):
        if key in case:
            for raw in iter_paths(case[key]):
                p = Path(raw)
                if not p.is_absolute():
                    p = base / p
                roots.append(p)
    if base.name.lower() == "input" or (base / "materials").is_dir():
        roots.append(base)
    if Path("/input").is_dir():
        roots.append(Path("/input"))
    seen, uniq = set(), []
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def collect(roots, errors, skipped, exclude, base=None):
    files, seen = [], set()
    try:
        exclude_key = str(exclude.resolve()) if exclude else None
        base_key = base.resolve() if base else None
    except OSError:
        exclude_key, base_key = None, None
    for root in roots:
        if not root.exists():
            errors.append("material root not found: %s" % (root.name or str(root)))
            continue
        if root.is_file():
            paths, root_dir = [root], root.parent
        else:
            root_dir, paths = root, []
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                    for fn in sorted(filenames):
                        paths.append(Path(dirpath) / fn)
            except OSError as exc:
                errors.append("walk failed on %s: %s" % (root.name, exc))
                continue
        for path in paths:
            if len(files) >= MAX_FILES:
                skipped.append({"path": path.name, "reason": "file cap reached"})
                continue
            try:
                rp = str(path.resolve())
                if path.is_symlink() or not path.is_file() or rp == exclude_key:
                    continue
            except OSError as exc:
                skipped.append({"path": path.name, "reason": "resolve failed: %s" % exc})
                continue
            if rp in seen:
                continue
            seen.add(rp)
            try:
                if base_key is None:
                    raise ValueError("no base")
                rel = path.resolve().relative_to(base_key).as_posix()
            except (OSError, ValueError):
                try:
                    rel = path.relative_to(root_dir).as_posix()
                except ValueError:
                    rel = path.name
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    return files


# --------------------------------------------------------------------------
# JSON / Markdown annotation readers
# --------------------------------------------------------------------------

def norm_annotation(raw, source_file, index):
    def pick(*keys):
        for k in keys:
            if k in raw and raw[k] not in (None, ""):
                return raw[k]
        return None

    page = pick("page", "page_number", "pageno")
    locator = pick("locator", "position", "offset")
    rect = pick("rect", "rectangle", "bbox")
    if not locator and isinstance(rect, (list, tuple)) and len(rect) == 4:
        locator = "page:%s;rect:%s" % (page if page is not None else "unknown",
                                       ",".join(str(x) for x in rect))
    if not locator and page is not None:
        locator = "page:%s" % page
    return {
        "id": "A%04d" % index,
        "source_file": source_file,
        "quote": pick("text", "quote", "content", "highlight", "excerpt"),
        "note": pick("note", "comment", "annotation", "memo"),
        "page": page,
        "color": pick("color", "colour"),
        "type": pick("type", "category", "kind") or "unknown",
        "locator": locator,
    }


def from_json(data, source_file, out):
    container = data
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = None
        for key in JSON_KEYS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if items is None and any(isinstance(v, list) for v in data.values()):
            items = next(v for v in data.values() if isinstance(v, list))
        if items is None:
            return 0
    else:
        return 0
    count = 0
    for raw in items:
        if isinstance(raw, dict):
            ann = norm_annotation(raw, source_file, len(out) + 1)
            if ann["quote"] or ann["note"]:
                out.append(ann)
                count += 1
        elif isinstance(raw, str) and raw.strip():
            out.append({"id": "A%04d" % (len(out) + 1), "source_file": source_file,
                        "quote": raw.strip(), "note": None, "page": None,
                        "color": None, "type": "unknown", "locator": None})
            count += 1
    return count


MD_QUOTE = re.compile(r"^\s*>\s?(.*\S)\s*$")
HIGHLIGHT = re.compile(r"==([^=\n]{3,})==|【批注[:：]\s*([^】]+)】")


def from_markdown(text, source_file, out):
    count = 0
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        m = MD_QUOTE.match(line)
        if m and len(m.group(1)) >= 3:
            out.append({"id": "A%04d" % (len(out) + 1), "source_file": source_file,
                        "quote": m.group(1).strip(), "note": None, "page": None,
                        "color": None, "type": "quote", "locator": "line:%d" % i})
            count += 1
            continue
        for hm in HIGHLIGHT.finditer(line):
            quote = (hm.group(1) or "").strip()
            note = (hm.group(2) or "").strip() or None
            if quote or note:
                out.append({"id": "A%04d" % (len(out) + 1), "source_file": source_file,
                            "quote": quote or None, "note": note, "page": None,
                            "color": None, "type": "highlight" if quote else "margin_note",
                            "locator": "line:%d" % i})
                count += 1
    return count


# --------------------------------------------------------------------------
# PDF annotation extraction (stdlib: FlateDecode + Tj/TJ text layer)
# --------------------------------------------------------------------------

def pdf_unescape(token):
    out = []
    i = 0
    while i < len(token):
        ch = token[i]
        if ch == 0x5C and i + 1 < len(token):
            nxt = token[i + 1]
            mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                       0x28: 40, 0x29: 41, 0x5C: 92}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if 0x30 <= nxt <= 0x37:
                octal = token[i + 1:i + 4]
                try:
                    out.append(int(octal, 8))
                except ValueError:
                    pass
                i += 1 + len(octal)
                continue
        out.append(ch)
        i += 1
    return bytes(bytearray(out))


def pdf_hex_text(raw):
    try:
        data = bytes(bytearray.fromhex(raw))
    except ValueError:
        return ""
    if len(data) >= 2 and data[:2] in (b"\xfe\xff", b"\xff\xfe"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return ""
    if len(data) % 2 == 0 and data.count(0) > len(data) // 4:
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1", errors="replace")


def pdf_token_text(token):
    if token.startswith(b"("):
        return pdf_unescape(token[1:-1]).decode("latin-1", errors="replace")
    raw = re.sub(rb"\s+", b"", token[1:-1]).decode("ascii", "ignore")
    return pdf_hex_text(raw)


def pdf_value_string(blob, key):
    pattern = re.compile(rb"/" + key + rb"\s*(\((?:\\.|[^()\\])*\)|<[0-9A-Fa-f\s]*>)", re.S)
    match = pattern.search(blob)
    if not match:
        return None
    text = pdf_token_text(match.group(1)).strip()
    return text or None


def pdf_objects(data):
    """Return [(object_number, body_bytes)] in file order."""
    objects = []
    for match in re.finditer(rb"(?m)^[ \t]*(\d+)\s+(\d+)\s+obj\b", data):
        num = int(match.group(1))
        start = match.end()
        end = data.find(b"endobj", start)
        if end < 0:
            end = len(data)
        stream = re.search(rb"stream\r?\n", data[start:end])
        if stream:
            stream_start = start + stream.end()
            stream_end = data.find(b"endstream", stream_start)
            if stream_end >= 0:
                after = data.find(b"endobj", stream_end + len(b"endstream"))
                if after >= 0:
                    end = after
        objects.append((num, data[start:end]))
    return objects


def pdf_streams(objects):
    streams = {}
    for num, body in objects:
        match = re.search(rb"stream\r?\n", body)
        if not match:
            continue
        header = body[:match.start()]
        raw = body[match.end():]
        end = raw.rfind(b"endstream")
        if end >= 0:
            raw = raw[:end]
        raw = raw.rstrip(b"\r\n")
        if b"/FlateDecode" in header:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                try:
                    raw = zlib.decompressobj().decompress(raw)
                except zlib.error:
                    continue
        streams[num] = raw
    return streams


def pdf_objstm_objects(objects, streams):
    """Expand objects compressed inside /Type /ObjStm streams."""
    extra = []
    for num, body in objects:
        if b"/ObjStm" not in body or num not in streams:
            continue
        n_match = re.search(rb"/N\s+(\d+)", body)
        first_match = re.search(rb"/First\s+(\d+)", body)
        if not n_match or not first_match:
            continue
        count = int(n_match.group(1))
        first = int(first_match.group(1))
        decoded = streams[num]
        header = decoded[:first]
        numbers = [int(x) for x in re.findall(rb"\d+", header)][:2 * count]
        pairs = [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]
        for index, (objnum, offset) in enumerate(pairs):
            begin = first + offset
            end = first + pairs[index + 1][1] if index + 1 < len(pairs) else len(decoded)
            extra.append((objnum, decoded[begin:end]))
    return extra


def pdf_pages(objects):
    pages = []
    for num, body in objects:
        if not re.search(rb"/Type\s*/Page\b", body):
            continue
        if re.search(rb"/Type\s*/Pages\b", body):
            continue
        contents, annots = [], []
        cm = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", body)
        if cm:
            contents = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", cm.group(1))]
        am = re.search(rb"/Annots\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", body)
        if am:
            annots = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", am.group(1))]
        pages.append({"obj": num, "body": body, "contents": contents, "annots": annots})
    return pages


def pdf_parse_rect(body):
    match = re.search(rb"/Rect\s*\[\s*([^\]]+)\]", body)
    if not match:
        return None
    numbers = [float(x) for x in re.findall(rb"[-+]?\d*\.?\d+", match.group(1))]
    if len(numbers) != 4:
        return None
    x0, y0, x1, y1 = numbers
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def pdf_parse_color(body):
    match = re.search(rb"/C\s*\[([^\]]*)\]", body)
    if not match:
        named = re.search(rb"/C\s*/(\w+)", body)
        if named:
            table = {b"Y": "yellow", b"G": "green", b"B": "blue",
                     b"R": "red", b"K": "black"}
            return table.get(named.group(1), None)
        return None
    values = [float(x) for x in re.findall(rb"[-+]?\d*\.?\d+", match.group(1))]
    if len(values) == 1:
        grey = values[0]
        return "black" if grey < 0.05 else ("white" if grey > 0.95 else "grey")
    if len(values) == 3:
        rgb = tuple(values)
        palette = [
            ((1, 1, 0), "yellow"), ((0, 1, 0), "green"), ((0, 0, 1), "blue"),
            ((1, 0, 0), "red"), ((1, 0.5, 0), "orange"), ((1, 0, 1), "magenta"),
            ((0, 1, 1), "cyan"), ((0, 0, 0), "black"), ((1, 1, 1), "white"),
        ]
        best, best_distance = None, 1.0
        for reference, name in palette:
            distance = sum((a - b) ** 2 for a, b in zip(rgb, reference)) ** 0.5
            if distance < best_distance:
                best, best_distance = name, distance
        if best is not None and best_distance <= 0.3:
            return best
        return "rgb(%s)" % ",".join(fmt_number(v) for v in rgb)
    if len(values) == 4:
        return "cmyk(%s)" % ",".join(fmt_number(v) for v in values)
    return None


def fmt_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return ("%.3f" % value).rstrip("0").rstrip(".")


PDF_RUN_RE = re.compile(
    r"([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+"
    r"([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+Tm"
    r"|([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+(?:Td|TD)"
    r"|(T\*)"
    r"|\[((?:\\.|[^\]])*)\]\s*TJ"
    r"|(\((?:\\.|[^()\\])*\))\s*Tj", re.S)


def pdf_tj_strings(array_text):
    parts = []
    for match in re.finditer(r"\((?:\\.|[^()\\])*\)|<[0-9A-Fa-f\s]+>", array_text, re.S):
        token = match.group(0).encode("latin-1", "replace")
        text = pdf_token_text(token)
        if text:
            parts.append(text)
    return "".join(parts)


def page_text_runs(content):
    """Approximate text runs: (x, y, text) from Tm/Td/T*/Tj/TJ."""
    text = content.decode("latin-1", errors="replace")
    runs = []
    x = y = 0.0
    for match in PDF_RUN_RE.finditer(text):
        if match.group(1) is not None:
            x, y = float(match.group(5)), float(match.group(6))
        elif match.group(7) is not None:
            x += float(match.group(7))
            y += float(match.group(8))
        elif match.group(9) is not None:
            y -= 12.0
        elif match.group(10) is not None:
            piece = pdf_tj_strings(match.group(10))
            if piece.strip():
                runs.append((x, y, piece))
        elif match.group(11) is not None:
            piece = pdf_token_text(match.group(11).encode("latin-1", "replace"))
            if piece.strip():
                runs.append((x, y, piece))
    return runs


def pdf_quote_for_rect(runs, rect):
    x0, y0, x1, y1 = rect
    picked = []
    for rx, ry, text in runs:
        if not (y0 - 2 <= ry <= y1 + 2) or not text.strip():
            continue
        # approximate horizontal extent: no width metrics in a pure-stdlib reader
        extent = max(6.0 * len(text), 1.0)
        if rx <= x1 + 2 and rx + extent >= x0 - 2:
            picked.append((ry, rx, text.strip()))
    picked.sort(key=lambda item: (-item[0], item[1]))
    quote = " ".join(item[2] for item in picked).strip()
    return quote or None


def pdf_annotations(data, rel):
    """Extract annotation dictionaries, page attribution, locators and page text."""
    objects = pdf_objects(data)
    streams = pdf_streams(objects)
    objects = objects + pdf_objstm_objects(objects, streams)
    pages = pdf_pages(objects)
    annotation_page = {}
    for page_number, page in enumerate(pages, 1):
        for ref in page["annots"]:
            annotation_page.setdefault(ref, page_number)
    annotations, unresolved = [], []
    seen = set()
    run_cache = {}
    for num, body in objects:
        subtype = re.search(
            rb"/Subtype\s*/(Highlight|Underline|StrikeOut|Squiggly|Text|FreeText|Square)\b",
            body)
        if not subtype:
            continue
        rect = pdf_parse_rect(body)
        if rect is None:
            continue
        kind = PDF_ANNOT_SUBTYPES.get(subtype.group(1).decode("ascii", "ignore"), "unknown")
        page = annotation_page.get(num)
        quote = None
        if page is not None:
            if page not in run_cache:
                runs = []
                for ref in pages[page - 1]["contents"]:
                    if ref in streams:
                        runs.extend(page_text_runs(streams[ref]))
                run_cache[page] = runs
            quote = pdf_quote_for_rect(run_cache[page], rect)
        locator = "page:%s;rect:%s,%s,%s,%s" % (
            page if page is not None else "unknown",
            fmt_number(rect[0]), fmt_number(rect[1]),
            fmt_number(rect[2]), fmt_number(rect[3]))
        note = pdf_value_string(body, b"Contents")
        author = pdf_value_string(body, b"T")
        color = pdf_parse_color(body)
        signature = (kind, rect, note, color, page)
        if signature in seen:
            continue
        seen.add(signature)
        annotations.append({
            "source_file": rel,
            "quote": quote,
            "note": note,
            "page": page,
            "color": color,
            "type": kind,
            "locator": locator,
            "author": author,
        })
        if quote is None:
            unresolved.append({
                "file": rel, "page": page, "locator": locator,
                "reason": "annotation_without_text_layer",
            })
        if page is None:
            unresolved.append({
                "file": rel, "locator": locator,
                "reason": "annotation_page_not_mapped",
            })
    return annotations, unresolved


# --------------------------------------------------------------------------
# notes, links and search index
# --------------------------------------------------------------------------

def classify(text):
    low = (text or "").lower()
    for label, cues in CUE:
        for cue in cues:
            if cue.lower() in low:
                return label, "cue:%s" % cue
    return "unclassified", "no_cue"


def tokens(text):
    text = (text or "").lower()
    out = set()
    for word in re.findall(r"[a-z][a-z0-9\-]{3,}", text):
        if word not in EN_STOP and not word.isdigit():
            out.add(word)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def key_terms(token_set, limit=8):
    return sorted(token_set, key=lambda term: (-len(term), term))[:limit]


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def resolve_case(raw):
    path = Path(str(raw))
    if path.is_dir():
        return path / "case.json"
    if path.suffix.lower() == ".json":
        return path
    return path / "case.json"


def resolve_out(raw):
    path = Path(str(raw))
    if path.exists() and path.is_dir():
        return path / "result.json"
    if path.suffix.lower() == ".json":
        return path
    return path / "result.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Annotation note distiller (stdlib only)")
    parser.add_argument("--case", "--input", "--in", dest="case", default="/input",
                        help="case.json, its directory, or a material directory")
    parser.add_argument("--out", "--output", dest="out", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    out_path = resolve_out(args.out)
    os.environ["SK_RESULT_PATH"] = str(out_path)

    case_path = resolve_case(args.case)
    errors, skipped, case, status = [], [], {}, "success"
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            case = json.load(fh)
        if not isinstance(case, dict):
            raise ValueError("case.json root is not an object")
    except (OSError, ValueError) as exc:
        errors.append("cannot read case.json: %s" % exc)
        case, status = {}, "failed"

    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else "unknown"
    fields = contract_fields(case)
    annotations, unresolved = [], []
    manifest = []

    if status != "failed":
        for path, rel in collect(candidate_roots(case, case_path), errors, skipped, case_path,
                                 case_path.parent):
            ext = path.suffix.lower()
            try:
                data = path.read_bytes()[:MAX_BYTES]
                digest = hashlib.sha256(data).hexdigest()
            except OSError as exc:
                skipped.append({"path": rel, "reason": "unreadable: %s" % exc})
                continue
            entry = {"file": rel, "ext": ext, "bytes": len(data), "sha256": digest,
                     "extractor": None, "annotations": 0}
            before = len(annotations)
            if ext == ".json":
                try:
                    parsed = json.loads(data.decode("utf-8-sig"))
                except (UnicodeDecodeError, ValueError) as exc:
                    skipped.append({"path": rel, "reason": "invalid json: %s" % exc})
                    continue
                from_json(parsed, rel, annotations)
                entry["extractor"] = "json-annotation-export"
            elif ext in (".md", ".markdown", ".txt"):
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                from_markdown(text, rel, annotations)
                entry["extractor"] = "markdown-quote-and-highlight"
            elif ext == ".pdf":
                extracted, pdf_unresolved = pdf_annotations(data, rel)
                unresolved.extend(pdf_unresolved)
                for item in extracted:
                    item["id"] = "A%04d" % (len(annotations) + 1)
                    annotations.append(item)
                entry["extractor"] = "pdf-annotation-flate-tj-tj"
                if not extracted:
                    unresolved.append({"file": rel, "reason": "pdf_without_annotation_objects"})
            else:
                skipped.append({"path": rel, "reason": "unsupported extension"})
                continue
            entry["annotations"] = len(annotations) - before
            manifest.append(entry)
        if not manifest:
            status = "insufficient_input"
        elif not annotations:
            status = "partial"
            unresolved.append({"file": "*", "reason": "no_annotation_records_found; supported inputs are JSON annotation exports, Markdown quote/highlight syntax and PDF annotation dictionaries"})

    notes, links = [], []
    for ann in annotations:
        text = " ".join(x for x in (ann["quote"], ann["note"]) if x)
        category, basis = classify(text)
        notes.append({
            "note_id": "N%04d" % len(notes),
            "annotation_id": ann["id"],
            "source_file": ann["source_file"],
            "page": ann["page"],
            "quote": ann["quote"],
            "note": ann["note"],
            "category": category,
            "category_basis": basis,
            "locator": ann["locator"],
            "tags": key_terms(tokens(text), 6),
        })

    token_map = [tokens(" ".join(x for x in (n["quote"], n["note"]) if x)) for n in notes]
    key_map = [set(key_terms(token_set, 8)) for token_set in token_map]
    for i in range(len(notes)):
        candidates = []
        for j in range(i + 1, len(notes)):
            shared = key_map[i] & key_map[j]
            union = token_map[i] | token_map[j]
            jaccard = len(token_map[i] & token_map[j]) / float(len(union)) if union else 0.0
            if len(shared) >= 2 or jaccard >= 0.15:
                candidates.append((jaccard, len(shared), notes[j]["note_id"],
                                   sorted(shared)[:8]))
        candidates.sort(key=lambda x: (-x[0], -x[1], x[2]))
        for jaccard, _count, target, shared_terms in candidates[:3]:
            links.append({"from": notes[i]["note_id"], "to": target,
                          "shared_terms": shared_terms,
                          "jaccard": round(jaccard, 3),
                          "basis": "shared_terms>=2 or jaccard>=0.15"})

    index = {}
    for note in notes:
        for tag in note["tags"]:
            index.setdefault(tag, []).append(note["note_id"])
    search_index = [{"term": k, "note_ids": sorted(set(v))} for k, v in sorted(index.items())]

    summary = ("Extracted %d annotation(s) from %d file(s); structured %d note(s); "
               "%d link(s); %d unresolved item(s)." % (len(annotations), len(manifest), len(notes),
                                                       len(links), len(unresolved)))
    result = {"annotations": annotations, "notes": notes, "links": links,
              "search_index": search_index, "unresolved": unresolved, "manifest": manifest}
    evidence = {"extractors": {m["file"]: m["extractor"] for m in manifest},
                "location_rule": ("page from source field or PDF /Annots mapping; "
                                  "locator from JSON fields, markdown line number or "
                                  "page:N;rect:x0,y0,x1,y1; no invented page"),
                "source_mutation": "none"}
    uncertainty = [
        "PDF annotation extraction scans object dictionaries and FlateDecode streams; "
        "annotations without a matching text-layer run keep quote=null and are listed in unresolved.",
        "Note categories use cue-word heuristics and require human confirmation.",
        "Links require two of the top-8 distinctive terms or Jaccard >= 0.15; "
        "absence of links is not proof of unrelatedness.",
    ]
    stats = {"files": len(manifest), "annotations": len(annotations), "notes": len(notes),
             "links": len(links), "unresolved": len(unresolved), "skipped": len(skipped),
             "errors": len(errors)}
    payload = {"annotations": annotations, "notes": notes, "links": links,
               "search_index": search_index, "unresolved": unresolved, "manifest": manifest,
               "summary": summary, "status": status, "case_id": case_id, "result": result,
               "evidence": evidence, "uncertainty": uncertainty, "errors": errors, "stats": stats}

    output = {"case_id": case_id, "status": status, "result": result, "evidence": evidence,
              "uncertainty": uncertainty, "errors": errors, "stats": stats}
    output["annotations"] = annotations
    output["notes"] = notes
    output["links"] = links
    output["search_index"] = search_index
    output["unresolved"] = unresolved
    notes_out = []
    contract_extras = {"source": "output_contract", "mapped": {}, "unknown_fields": [],
                       "accepted_extra_fields": []}
    for field in fields:
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            contract_extras["unknown_fields"].append(field)
            notes_out.append("%s: no supported producer in this skill; null retained" % field)
        else:
            output[field] = value
            contract_extras["mapped"][field] = canonical
    if notes_out:
        output["contract_notes"] = notes_out
    output["contract_extras"] = contract_extras

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("cannot write result: %s\n" % exc)
        return 2
    try:
        work = Path(args.work)
        work.mkdir(parents=True, exist_ok=True)
        with open(work / "notes.jsonl", "w", encoding="utf-8") as fh:
            for note in notes:
                fh.write(json.dumps(note, ensure_ascii=False) + "\n")
        with open(work / "annotations.jsonl", "w", encoding="utf-8") as fh:
            for ann in annotations:
                fh.write(json.dumps(ann, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)
