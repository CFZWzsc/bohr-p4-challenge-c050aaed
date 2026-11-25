# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-c050aaed` (track: `research-library`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `annotation-note-distiller`
- Summary: Use when a research-library case supplies PDFs, annotation exports, or reading notes and asks to extract highlights, structure them into sourced notes, link related items, or make them searchable; run scripts/run_skill.py for a deterministic contract-preserving result.json.

## Contents

| File |
|------|
| `SKILL.md` |
| `examples/demo-case/annotations.json` |
| `examples/demo-case/case.json` |
| `examples/demo-case/materials/mini-annotated.pdf` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
