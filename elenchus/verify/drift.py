"""Detect UI drift in the ERP forms before an agent run.

A form page is reduced to a readable structure: each form's fields in page
order (tag, input type, input name, label) and buttons in page order, plus the
kind of size widget. The structure hashes to a stable fingerprint. Option
lists, form actions and the order id are left out, so the hash does not change
with ERP data.

Two pages are fingerprinted: the new-order form and a read-only preview of the
add-line form (the real add-line form only exists on a draft order page).

Usage:
    python -m elenchus.verify.drift record [--base-url URL] [--baseline PATH]
    python -m elenchus.verify.drift check  [--base-url URL] [--variant NAME]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import httpx

DEFAULT_BASELINE = Path("data/fingerprints/baseline.json")
PAGES = {"new_order": "/orders/new", "line_form": "/orders/line-form-preview"}
SIZE_CODES = {"S", "M", "L", "XL", "2XL"}
FORMAT_VERSION = 1


# --- parsing ------------------------------------------------------------------

class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._label_for: str | None = None
        self._label_text: list[str] | None = None
        self._label_wraps = False
        self._label_texts: dict[str, str] = {}
        self._select: dict | None = None
        self._select_values: set[str] = set()
        self._button: dict | None = None
        self.size_select = False

    # Fields and buttons are only collected inside a form.
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = {"fields": [], "buttons": []}
            self.forms.append(self._form)
        elif self._form is None:
            return
        elif tag == "label":
            self._label_for = a.get("for")
            self._label_text = []
            self._label_wraps = self._label_for is None
        elif tag == "input":
            kind = (a.get("type") or "text").lower()
            if kind in ("submit", "reset", "button"):
                text = a.get("value") or kind.title()
                self._form["buttons"].append({"type": kind, "text": text})
            elif kind != "hidden":
                self._add_field("input", kind, a)
        elif tag == "select":
            self._select = self._add_field("select", "select", a)
            self._select_values = set()
        elif tag == "option" and self._select is not None:
            self._select_values.add(a.get("value", ""))
        elif tag == "textarea":
            self._add_field("textarea", "textarea", a)
        elif tag == "button":
            self._button = {"type": (a.get("type") or "submit").lower(), "text": []}

    def _add_field(self, tag: str, kind: str, a: dict) -> dict:
        label = ""
        if self._label_text is not None and self._label_wraps:
            label = " ".join("".join(self._label_text).split())
        entry = {
            "tag": tag,
            "type": kind,
            "name": a.get("name", ""),
            "label": label,
            "_id": a.get("id"),
        }
        self._form["fields"].append(entry)
        return entry

    def handle_data(self, data):
        if self._label_text is not None:
            self._label_text.append(data)
        if self._button is not None:
            self._button["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "label" and self._label_text is not None:
            if self._label_for:
                self._label_texts[self._label_for] = " ".join(
                    "".join(self._label_text).split()
                )
            self._label_text = None
            self._label_for = None
        elif tag == "select" and self._select is not None:
            if SIZE_CODES <= self._select_values:
                self.size_select = True
            self._select = None
        elif tag == "button" and self._button is not None and self._form is not None:
            text = " ".join("".join(self._button["text"]).split())
            self._form["buttons"].append({"type": self._button["type"], "text": text})
            self._button = None
        elif tag == "form":
            self._form = None


def _size_widget(forms: list[dict], size_select: bool) -> str:
    """"grid" for a run of 3+ adjacent number inputs, "dropdown" for a size select."""
    if size_select:
        return "dropdown"
    for form in forms:
        run = 0
        for f in form["fields"]:
            run = run + 1 if (f["tag"], f["type"]) == ("input", "number") else 0
            if run >= 3:
                return "grid"
    return "none"


def page_structure(html: str) -> dict:
    """Readable structure of one page's forms."""
    parser = _FormParser()
    parser.feed(html)
    parser.close()
    forms = []
    for form in parser.forms:
        fields = []
        for f in form["fields"]:
            label = f["label"] or parser._label_texts.get(f["_id"] or "", "")
            fields.append(
                {"tag": f["tag"], "type": f["type"], "name": f["name"], "label": label}
            )
        forms.append({"fields": fields, "buttons": form["buttons"]})
    return {"forms": forms, "size_widget": _size_widget(forms, parser.size_select)}


def fingerprint_pages(pages: dict[str, str]) -> dict:
    """``{"hash": ..., "structure": ...}`` from page name -> HTML."""
    structure = {
        "version": FORMAT_VERSION,
        "pages": {name: page_structure(html) for name, html in sorted(pages.items())},
    }
    return {"hash": structure_hash(structure), "structure": structure}


def structure_hash(structure: dict) -> str:
    canonical = json.dumps(structure, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def fetch_fingerprint(base_url: str, variant: str = "baseline") -> dict:
    query = "" if variant == "baseline" else f"?variant={variant}"
    pages = {}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=10) as client:
        for name, path in PAGES.items():
            resp = client.get(path + query)
            resp.raise_for_status()
            pages[name] = resp.text
    return fingerprint_pages(pages)


# --- baseline file ------------------------------------------------------------

def save_baseline(fp: dict, path: str | Path = DEFAULT_BASELINE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_baseline(path: str | Path = DEFAULT_BASELINE) -> dict:
    fp = json.loads(Path(path).read_text(encoding="utf-8"))
    if structure_hash(fp["structure"]) != fp["hash"]:
        raise ValueError(f"{path}: hash does not match structure (file edited by hand?)")
    return fp


# --- comparing ----------------------------------------------------------------

def _fdesc(f: dict) -> str:
    return f"{f['label'] or '(no label)'} [{f['name'] or '(no name)'}]"


def _pair_fields(base: list[dict], cur: list[dict]):
    """Pair baseline and current fields: by name, then label, then position."""
    pairs: list[tuple[int, int]] = []
    ub, uc = list(range(len(base))), list(range(len(cur)))
    for key in ("name", "label"):
        for i in list(ub):
            if not base[i][key]:
                continue
            for j in uc:
                if cur[j][key] == base[i][key]:
                    pairs.append((i, j))
                    ub.remove(i)
                    uc.remove(j)
                    break
    if len(ub) == len(uc):  # same number left over: treat as renamed in place
        pairs.extend(zip(ub, uc))
        ub, uc = [], []
    return sorted(pairs), ub, uc


def _compare_form(where: str, base: dict, cur: dict) -> list[dict]:
    out: list[dict] = []

    def add(kind: str, detail: str) -> None:
        out.append({"where": where, "kind": kind, "detail": detail})

    pairs, removed, added = _pair_fields(base["fields"], cur["fields"])
    for i, j in pairs:
        b, c = base["fields"][i], cur["fields"][j]
        if b["label"] != c["label"]:
            add("label", f"label {b['label']!r} -> {c['label']!r} (input {c['name']!r})")
        if b["name"] != c["name"]:
            add("input_name", f"input name {b['name']!r} -> {c['name']!r} ({b['label']!r})")
        if (b["tag"], b["type"]) != (c["tag"], c["type"]):
            add(
                "input_type",
                f"{_fdesc(b)} changed from {b['tag']}/{b['type']} to {c['tag']}/{c['type']}",
            )
    for i in removed:
        add("removed", f"field removed: {_fdesc(base['fields'][i])}")
    for j in added:
        add("added", f"field added: {_fdesc(cur['fields'][j])}")
    if [j for _, j in pairs] != sorted(j for _, j in pairs):
        before = ", ".join(base["fields"][i]["label"] or base["fields"][i]["name"] for i, _ in pairs)
        after = ", ".join(
            cur["fields"][j]["label"] or cur["fields"][j]["name"]
            for _, j in sorted(pairs, key=lambda p: p[1])
        )
        add("element_order", f"field order changed: {before} -> {after}")

    bb = [(b["type"], b["text"]) for b in base["buttons"]]
    cb = [(b["type"], b["text"]) for b in cur["buttons"]]
    if bb != cb:
        if sorted(bb) == sorted(cb):
            add(
                "button_order",
                "button order changed: "
                f"{[t for _, t in bb]} -> {[t for _, t in cb]}",
            )
        elif len(bb) == len(cb):
            for (_, bt), (_, ct) in zip(bb, cb):
                if bt != ct:
                    add("button_text", f"button {bt!r} -> {ct!r}")
        else:
            add("buttons", f"buttons changed: {[t for _, t in bb]} -> {[t for _, t in cb]}")
    return out


def compare(baseline: dict, current: dict) -> list[dict]:
    """What changed between two structures, as ``{where, kind, detail}`` dicts."""
    out: list[dict] = []
    for name in sorted(set(baseline["pages"]) | set(current["pages"])):
        b, c = baseline["pages"].get(name), current["pages"].get(name)
        if b is None or c is None:
            out.append({"where": name, "kind": "page", "detail": "page missing from one side"})
            continue
        if len(b["forms"]) != len(c["forms"]):
            out.append({
                "where": name, "kind": "forms",
                "detail": f"form count {len(b['forms'])} -> {len(c['forms'])}",
            })
        for n, (bf, cf) in enumerate(zip(b["forms"], c["forms"]), start=1):
            out.extend(_compare_form(f"{name}/form{n}", bf, cf))
        if b["size_widget"] != c["size_widget"]:
            out.append({
                "where": name, "kind": "size_widget",
                "detail": f"size widget {b['size_widget']} -> {c['size_widget']}",
            })
    return out


@dataclass(frozen=True)
class DriftReport:
    baseline_hash: str
    current_hash: str
    changes: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.changes and self.baseline_hash == self.current_hash

    def format(self) -> str:
        if self.ok:
            return f"no drift (fingerprint {self.current_hash})"
        lines = [f"UI drift: fingerprint {self.baseline_hash} -> {self.current_hash}"]
        lines += [f"  [{c['where']}] {c['detail']}" for c in self.changes]
        return "\n".join(lines)


class DriftBlocked(RuntimeError):
    def __init__(self, report: DriftReport) -> None:
        super().__init__(report.format())
        self.report = report


def check_drift(
    base_url: str, variant: str = "baseline", baseline_path: str | Path = DEFAULT_BASELINE
) -> DriftReport:
    baseline = load_baseline(baseline_path)
    current = fetch_fingerprint(base_url, variant)
    return DriftReport(
        baseline["hash"], current["hash"], compare(baseline["structure"], current["structure"])
    )


def require_no_drift(
    base_url: str, variant: str = "baseline", baseline_path: str | Path = DEFAULT_BASELINE
) -> DriftReport:
    """Return the clean report, or raise ``DriftBlocked`` naming what changed."""
    report = check_drift(base_url, variant, baseline_path)
    if not report.ok:
        raise DriftBlocked(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("record", "check"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    args = parser.parse_args()
    if args.action == "record":
        fp = fetch_fingerprint(args.base_url, args.variant)
        print(f"wrote {save_baseline(fp, args.baseline)} (fingerprint {fp['hash']})")
        return
    report = check_drift(args.base_url, args.variant, args.baseline)
    print(report.format())
    sys.exit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
