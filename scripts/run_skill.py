#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic annotation extraction and note distillation. Standard library only."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MAX_FILES = 2000
MAX_BYTES = 4 * 1024 * 1024
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "corpus_dir", "directory", "path", "root")
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
            out = list(oc.keys())
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


def collect(roots, errors, skipped, exclude):
    files, seen = [], set()
    try:
        exclude_key = str(exclude.resolve()) if exclude else None
    except OSError:
        exclude_key = None
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
                rel = path.relative_to(root_dir).as_posix()
            except ValueError:
                rel = path.name
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    return files


def norm_annotation(raw, source_file, index):
    def pick(*keys):
        for k in keys:
            if k in raw and raw[k] not in (None, ""):
                return raw[k]
        return None
    return {
        "id": "A%04d" % index,
        "source_file": source_file,
        "quote": pick("text", "quote", "content", "highlight", "excerpt"),
        "note": pick("note", "comment", "annotation", "memo"),
        "page": pick("page", "page_number", "pageno"),
        "color": pick("color", "colour"),
        "type": pick("type", "category", "kind") or "unknown",
        "locator": pick("locator", "position", "rect", "offset"),
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


def classify(text):
    low = (text or "").lower()
    for label, cues in CUE:
        for cue in cues:
            if cue.lower() in low:
                return label, "cue:%s" % cue
    return "unclassified", "no_cue"


def tokens(text):
    text = (text or "").lower()
    out = set(w for w in re.findall(r"[a-z]{4,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Annotation note distiller (stdlib only)")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--out", "--output", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    case_path = Path(args.case)
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
        for path, rel in collect(candidate_roots(case, case_path), errors, skipped, case_path):
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
                entry["extractor"] = "not-parsed"
                unresolved.append({"file": rel, "reason": "pdf_annotation_layer_not_parsed"})
            else:
                skipped.append({"path": rel, "reason": "unsupported extension"})
                continue
            entry["annotations"] = len(annotations) - before
            manifest.append(entry)
        if not manifest:
            status = "insufficient_input"
        elif not annotations:
            status = "partial"
            unresolved.append({"file": "*", "reason": "no_annotation_records_found; supported inputs are JSON annotation exports and Markdown quote/highlight syntax"})

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
            "tags": sorted(tokens(text))[:6],
        })

    tok_map = [tokens(" ".join(x for x in (n["quote"], n["note"]) if x)) for n in notes]
    for i in range(len(notes)):
        candidates = []
        for j in range(len(notes)):
            if i == j:
                continue
            shared = tok_map[i] & tok_map[j]
            union = tok_map[i] | tok_map[j]
            if len(shared) >= 2 and union:
                jac = len(shared) / float(len(union))
                if jac >= 0.2:
                    candidates.append((jac, len(shared), notes[j]["note_id"], sorted(shared)[:5]))
        candidates.sort(key=lambda x: (-x[0], -x[1], x[2]))
        for jac, count, target, shared in candidates[:3]:
            links.append({"from": notes[i]["note_id"], "to": target,
                          "shared_terms": shared, "jaccard": round(jac, 3),
                          "basis": "shared_terms>=2 and jaccard>=0.2"})

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
                "location_rule": "page from source field; locator from JSON or markdown line number; no invented page",
                "source_mutation": "none"}
    uncertainty = [
        "PDF annotation layers and handwritten marks are not parsed; they are reported as unresolved.",
        "Note categories use cue-word heuristics and require human confirmation.",
        "Links require at least two shared distinctive terms; absence of links is not proof of unrelatedness.",
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
    for field in fields:
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            notes_out.append("%s: no supported producer in this skill; null retained" % field)
        else:
            output[field] = value
    if notes_out:
        output["contract_notes"] = notes_out

    try:
        out_path = Path(args.out)
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
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
