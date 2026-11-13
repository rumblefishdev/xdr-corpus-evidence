#!/usr/bin/env python3
"""List every XDR variation point a ledger reader can meet, and which ones the corpus covers.

PROTOTYPE. A "coverage target" is one place where valid XDR can take different shapes:
  - arm       one arm of a union (incl. the ext v0/v1/... slots that protocol upgrades switch on);
  - optional  an optional field (T* x), as two targets: present and absent.
Only types reachable from LedgerCloseMetaBatch count: that is what an indexer reads from the data lake.

For each target:
  - introduced  the first stellar-xdr protocol tag (v19..v30) that has it; "<=19" if v19 already had it;
  - cap         CAPs named in the stellar-xdr commits that added it in that protocol (best effort; blank for
                <=19, where history starts, and for numbered v0/v1 slots, whose numbers are too common to search);
  - covered_by  corpus entries whose files contain it (any transaction in the ledger, not only the target tx).

Coverage comes from walking the stellar CLI's JSON decode of each entry's files alongside the parsed XDR schema.

Usage: python3 -I corpus/tools/coverage-targets.py [--out corpus/coverage]
Needs: git, network on first run (clones stellar/stellar-xdr into .cache/), the stellar CLI (get-stellar-cli.sh).
"""
import argparse
import collections
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
XDR_REPO = "https://github.com/stellar/stellar-xdr"
XDR_DIR = ROOT / ".cache" / "stellar-xdr"
TARGET_TAG = "v30.0"  # current protocol; pinned for reproducibility
TAGS = ["v19", "v20", "v21.0", "v22.0", "v23.0", "v24.0", "v25.0", "v26.0", "v27.0", "v28.0", "v30.0"]
ROOT_TYPE = "LedgerCloseMetaBatch"

# rs-stellar-xdr renders these unions as strkey strings; the first letter tells the arm.
STRKEY_ARMS = {
    "PublicKey": {"G": "PUBLIC_KEY_TYPE_ED25519"},
    "MuxedAccount": {"G": "KEY_TYPE_ED25519", "M": "KEY_TYPE_MUXED_ED25519"},
    "SignerKey": {"G": "SIGNER_KEY_TYPE_ED25519", "T": "SIGNER_KEY_TYPE_PRE_AUTH_TX",
                  "X": "SIGNER_KEY_TYPE_HASH_X", "P": "SIGNER_KEY_TYPE_ED25519_SIGNED_PAYLOAD"},
    "SCAddress": {"G": "SC_ADDRESS_TYPE_ACCOUNT", "C": "SC_ADDRESS_TYPE_CONTRACT",
                  "M": "SC_ADDRESS_TYPE_MUXED_ACCOUNT", "B": "SC_ADDRESS_TYPE_CLAIMABLE_BALANCE",
                  "L": "SC_ADDRESS_TYPE_LIQUIDITY_POOL"},
    "ClaimableBalanceID": {"B": "CLAIMABLE_BALANCE_ID_TYPE_V0"},
}

# ---------------------------------------------------------------- XDR parser

TOKEN = re.compile(r"\s*(?:(//[^\n]*|/\*.*?\*/)|([A-Za-z_][A-Za-z0-9_]*|0x[0-9A-Fa-f]+|-?\d+)|(.))", re.S)


def tokenize(text):
    lines, skip = [], 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#ifdef") or s.startswith("#if "):
            skip += 1
            continue
        if s.startswith("#endif"):
            skip = max(0, skip - 1)
            continue
        if skip or s.startswith("%") or s.startswith("#"):
            continue
        lines.append(line)
    out = []
    for m in TOKEN.finditer("\n".join(lines)):
        if m.group(1):
            continue
        t = m.group(2) or m.group(3)
        if t and not t.isspace():
            out.append(t)
    return out


class Parser:
    """Recursive descent over the XDR language subset stellar-xdr uses. Fills self.types:
    name -> ("struct", [field]) | ("union", disc_type, [(cases, field|None)]) | ("enum", [names]) | ("typedef", field)
    field = (name, type, decor) with decor in {"", "opt", "array<N>", "array[N]"}."""

    def __init__(self):
        self.types = {}

    def parse(self, tokens):
        self.t, self.i = tokens, 0
        while self.i < len(self.t):
            k = self.next()
            if k == "namespace":
                self.next(); self.expect("{")
            elif k == "}":
                pass
            elif k == "const":
                self.next(); self.expect("="); self.next(); self.expect(";")
            elif k == "typedef":
                f = self.declaration("")
                self.types[f[0]] = ("typedef", f)
                self.expect(";")
            elif k in ("struct", "union", "enum"):
                name = self.next()
                self.body(k, name)
                self.expect(";")
            elif k == ";":
                pass
            else:
                raise SyntaxError(f"unexpected {k!r} near {self.t[self.i:self.i + 8]}")

    def next(self):
        self.i += 1
        return self.t[self.i - 1]

    def peek(self):
        return self.t[self.i]

    def expect(self, s):
        k = self.next()
        if k != s:
            raise SyntaxError(f"expected {s!r}, got {k!r} near {self.t[self.i - 5:self.i + 5]}")

    def body(self, kind, name):
        if kind == "enum":
            self.expect("{")
            names = []
            while True:
                n = self.next()
                if n == "}":
                    break
                names.append(n)
                if self.peek() == "=":
                    self.next(); self.next()
                if self.peek() == ",":
                    self.next()
            self.types[name] = ("enum", names)
        elif kind == "struct":
            self.expect("{")
            fields = []
            while self.peek() != "}":
                fields.append(self.declaration(name))
                self.expect(";")
            self.next()
            self.types[name] = ("struct", fields)
        else:
            self.expect("switch"); self.expect("(")
            disc = self.typename()
            self.next()  # discriminant name
            self.expect(")"); self.expect("{")
            arms = []
            while self.peek() != "}":
                cases = []
                while self.peek() in ("case", "default"):
                    if self.next() == "case":
                        cases.append(self.next())
                    else:
                        cases.append("default")
                    self.expect(":")
                if self.peek() == "void":
                    self.next()
                    arms.append((cases, None))
                else:
                    arms.append((cases, self.declaration(name)))
                self.expect(";")
            self.next()
            self.types[name] = ("union", disc, arms)

    def typename(self):
        t = self.next()
        if t == "unsigned":
            t = "unsigned " + self.next()
        return t

    def declaration(self, parent):
        t = self.typename()
        if t in ("struct", "union", "enum"):  # inline anonymous type: name it <parent>.<field>, fixed below
            anon = f"__anon{self.i}"
            self.body(t, anon)
            t = anon
        decor = ""
        if self.peek() == "*":
            self.next()
            decor = "opt"
        name = self.next()
        if self.peek() in ("<", "["):
            o = self.next()
            n = ""
            while self.peek() not in (">", "]"):
                n += self.next()
            self.next()
            decor = f"array{o}{n}{'>' if o == '<' else ']'}"
        if t.startswith("__anon"):
            real = f"{parent}.{name}"
            self.types[real] = self.types.pop(t)
            t = real
        return (name, t, decor)


def load_schema(tag):
    files = subprocess.run(["git", "-C", XDR_DIR, "ls-tree", "--name-only", tag], check=True,
                           capture_output=True, text=True).stdout.split()
    p = Parser()
    for f in sorted(files):
        if f.endswith(".x"):
            src = subprocess.run(["git", "-C", XDR_DIR, "show", f"{tag}:{f}"], check=True,
                                 capture_output=True, text=True).stdout
            p.parse(tokenize(src))
    return p.types


# ---------------------------------------------------------------- targets

def reachable(types, root):
    seen, stack = set(), [root]
    while stack:
        n = stack.pop()
        if n in seen or n not in types:
            continue
        seen.add(n)
        d = types[n]
        if d[0] == "struct":
            stack += [f[1] for f in d[1]]
        elif d[0] == "union":
            stack.append(d[1])
            stack += [f[1] for _, f in d[2] if f]
        elif d[0] == "typedef":
            stack.append(d[1][1])
    return seen


def targets_of(types, names):
    out = []
    for n in sorted(names):
        d = types[n]
        if d[0] == "union":
            for cases, field in d[2]:
                for c in cases:
                    out.append(("arm", n, c, field[1] if field else "void"))
        fields = d[1] if d[0] == "struct" else [f for _, f in d[2] if f] if d[0] == "union" else []
        for f in fields:
            if f[2] == "opt":
                out.append(("optional", n, f"{f[0]}=present", f[1]))
                out.append(("optional", n, f"{f[0]}=absent", f[1]))
    return out


def ensure_repo():
    if not (XDR_DIR / ".git").exists():
        XDR_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", XDR_REPO, XDR_DIR], check=True)
    have = subprocess.run(["git", "-C", XDR_DIR, "tag"], capture_output=True, text=True).stdout.split()
    if not set(TAGS) <= set(have):
        subprocess.run(["git", "-C", XDR_DIR, "fetch", "-q", "--tags"], check=True)


def cap_for(case_name, intro_tag):
    """CAP named in the stellar-xdr commits between the previous protocol tag and intro_tag that touch this
    identifier. Restricting the range avoids crediting a name reused in a new union to its first use elsewhere."""
    prev = TAGS[TAGS.index(intro_tag) - 1]
    log = subprocess.run(["git", "-C", XDR_DIR, "log", "--format=%s%n%b", "-S", case_name,
                          f"{prev}..{intro_tag}", "--", "*.x"], capture_output=True, text=True).stdout
    caps = sorted({int(n) for n in re.findall(r"CAP[-_ ]?0*(\d{1,3})\b", log, re.I)})
    return " ".join(f"CAP-{n:04d}" for n in caps)


# ---------------------------------------------------------------- coverage walk

def snake(s):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", s).lower()


def enum_prefix(names):
    if len(names) < 2:
        return ""
    parts = [n.split("_") for n in names]
    pre = []
    for toks in zip(*parts):
        if len(set(toks)) != 1:
            break
        pre.append(toks[0])
    if pre and any(len(p) == len(pre) for p in parts):  # never strip a whole name
        pre = pre[:-1]
    return "_".join(pre) + "_" if pre else ""


class Walker:
    def __init__(self, types):
        self.types = types
        self.hits = collections.Counter()
        self.unmatched = collections.Counter()

    def resolve(self, t):
        while t in self.types and self.types[t][0] == "typedef" and self.types[t][1][2] == "":
            t = self.types[t][1][1]
        return t

    def arm_keys(self, union):
        """JSON key (snake_case variant) -> case name, as rs-stellar-xdr names union variants."""
        _, disc, arms = self.types[union]
        disc = self.resolve(disc)
        members = self.types[disc][1] if disc in self.types and self.types[disc][0] == "enum" else None
        pre = enum_prefix(members) if members else ""
        keys = {}
        for cases, field in arms:
            for c in cases:
                cands = {c.lower(), snake(c)}  # snake: txSUCCESS -> tx_success
                if c.lstrip("-").isdigit():
                    cands.add(f"v{c}")
                if pre and c.startswith(pre):
                    cands.add(c[len(pre):].lower())
                if field:
                    cands.add(snake(field[0]))
                for k in cands:
                    keys.setdefault(k, c)
        return keys

    def walk(self, t, v, decor=""):
        if decor.startswith("array"):
            if isinstance(v, list):
                for x in v:
                    self.walk(t, x)
            return
        t = self.resolve(t)
        if t in self.types and self.types[t][0] == "typedef":  # typedef with decoration
            f = self.types[t][1]
            return self.walk(f[1], v, f[2])
        d = self.types.get(t)
        if not d or v is None:
            return
        if d[0] == "struct" and isinstance(v, dict):
            for name, ft, fdecor in d[1]:
                fv = v.get(snake(name), v.get(name))
                if fdecor == "opt":
                    self.hits[("optional", t, f"{name}={'absent' if fv is None else 'present'}")] += 1
                    if fv is not None:
                        self.walk(ft, fv)
                else:
                    self.walk(ft, fv, fdecor)
        elif d[0] == "union":
            keys = self.arm_keys(t)
            arms = {c: f for cases, f in d[2] for c in cases}
            if isinstance(v, str):
                if t in STRKEY_ARMS and v[:1] in STRKEY_ARMS[t]:
                    self.hits[("arm", t, STRKEY_ARMS[t][v[:1]])] += 1
                elif v in keys:
                    self.hits[("arm", t, keys[v])] += 1
                else:
                    self.unmatched[(t, v[:24])] += 1
            elif isinstance(v, dict) and len(v) == 1:
                k, inner = next(iter(v.items()))
                if k in keys:
                    c = keys[k]
                    self.hits[("arm", t, c)] += 1
                    f = arms.get(c) or arms.get("default")
                    if f:
                        self.walk(f[1], inner, f[2])
                else:
                    self.unmatched[(t, k)] += 1
            elif isinstance(v, dict) and len(arms) == 1:  # single-arm union rendered as its arm's value
                c, f = next(iter(arms.items()))
                self.hits[("arm", t, c)] += 1
                if f:
                    self.walk(f[1], v, f[2])
            else:
                self.unmatched[(t, type(v).__name__)] += 1


def decode(stellar, xdr_type, path):
    out = subprocess.run([stellar, "xdr", "decode", "--type", xdr_type, "--input", "single", "--output", "json",
                          str(path)], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "corpus" / "coverage"))
    ap.add_argument("--no-cap", action="store_true", help="skip the git-log CAP lookup (faster)")
    args = ap.parse_args()

    ensure_repo()
    schemas = {tag: load_schema(tag) for tag in TAGS}
    types = schemas[TARGET_TAG]
    reach = reachable(types, ROOT_TYPE)
    targets = targets_of(types, reach)
    all_targets = targets_of(types, types.keys())

    def introduced(kind, owner, case):
        for tag in TAGS:
            ts = schemas[tag]
            if owner not in ts:
                continue
            if kind == "arm" and any(case in cs for cs, _ in ts[owner][2] if ts[owner][0] == "union"):
                return tag
            if kind == "optional":
                fname = case.split("=")[0]
                d = ts[owner]
                fields = d[1] if d[0] == "struct" else [f for _, f in d[2] if f] if d[0] == "union" else []
                if any(f[0] == fname and f[2] == "opt" for f in fields):
                    return tag
        return "?"

    stellar = os.environ.get("STELLAR") or subprocess.run(
        [str(ROOT / "corpus/tools/get-stellar-cli.sh")], check=True, capture_output=True, text=True
    ).stdout.strip().splitlines()[-1]
    manifest = json.loads((ROOT / "corpus/manifest.json").read_text())
    covered = collections.defaultdict(set)
    unmatched = collections.Counter()
    for e in manifest["entries"]:
        w = Walker(types)
        for f in e["files"]:
            if f["encoding"] != "xdr" or f.get("derived_from"):
                continue
            w.walk(f["xdr_type"], decode(stellar, f["xdr_type"], ROOT / f["path"]))
        for k in w.hits:
            covered[k].add(e["id"])
        unmatched.update(w.unmatched)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind, owner, case, payload in targets:
        intro = introduced(kind, owner, case)
        cap = "" if args.no_cap or kind != "arm" or intro in ("v19", "?") or case == "default" \
            or case.lstrip("-").isdigit() else cap_for(case, intro)
        rows.append({
            "kind": kind, "type": owner, "case": case, "payload": payload,
            "introduced": "<=19" if intro == "v19" else intro.lstrip("v").split(".")[0],
            "cap": cap,
            "covered_by": " ".join(sorted(covered.get((kind, owner, case), ()))),
        })
    with open(out / "targets.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)

    # summary
    by_kind = collections.Counter(r["kind"] for r in rows)
    cov_kind = collections.Counter(r["kind"] for r in rows if r["covered_by"])
    by_proto = collections.Counter(r["introduced"] for r in rows)
    cov_proto = collections.Counter(r["introduced"] for r in rows if r["covered_by"])
    unions = {r["type"] for r in rows if r["kind"] == "arm"}
    ext_arms = [r for r in rows if r["kind"] == "arm" and r["case"].lstrip("-").isdigit()]
    lines = [
        f"# Coverage targets (prototype, generated by corpus/tools/coverage-targets.py)",
        "",
        f"stellar-xdr `{TARGET_TAG}`: {len(types)} types; {len(reach)} reachable from `{ROOT_TYPE}`.",
        f"All types: {sum(1 for k, *_ in all_targets if k == 'arm')} union arms; "
        f"reachable from `{ROOT_TYPE}`: {by_kind['arm']} arms in {len(unions)} unions "
        f"({len(ext_arms)} of them integer-versioned `v0/v1/…` slots), and {by_kind['optional'] // 2} optional fields.",
        "",
        f"| Target kind | Targets | Covered by the {len(manifest['entries'])} entries |",
        "| --- | --- | --- |",
    ]
    for k in ("arm", "optional"):
        lines.append(f"| {k} | {by_kind[k]} | {cov_kind[k]} ({100 * cov_kind[k] / by_kind[k]:.0f}%) |")
    lines += ["", "| Introduced in protocol | Targets | Covered |", "| --- | --- | --- |"]
    for p in sorted(by_proto, key=lambda x: (x != "<=19", int(x) if x.isdigit() else 0)):
        lines.append(f"| {p} | {by_proto[p]} | {cov_proto[p]} |")
    lines += ["", "Per-target list: `targets.csv` (kind, type, case, payload, introduced, cap, covered_by)."]
    if unmatched:
        lines += ["", f"Walker could not place {sum(unmatched.values())} JSON nodes "
                      f"({len(unmatched)} distinct); top: " +
                  ", ".join(f"`{t}:{k}`×{n}" for (t, k), n in unmatched.most_common(8))]
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
