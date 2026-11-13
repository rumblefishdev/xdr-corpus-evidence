#!/usr/bin/env python3
"""Build corpus/manifest.json from the corpus files.

Every expected value is derived from `stellar xdr decode` of the committed ledger files.
The only hand-written inputs are in ENTRIES below: which tx and operation an entry is about
(the selector), CAP/SEP tags, the protocol a CAP was introduced in, and issue links.

For each entry the script also checks:
  * the .xdr.zst file decompresses to the committed .xdr file,
  * decode -> encode of the .xdr file is byte-exact,
  * the target tx is in the ledger and succeeded.

Usage: corpus/tools/build-manifest.py [--check]
       corpus/tools/build-manifest.py --regen-check <entry-id> <dir>
  --check        do not write; exit 1 if corpus/manifest.json differs from what would be written
  --regen-check  build the entry from a regenerated <dir> (see corpus/tools/regen-entry.sh) and compare it with
                 the committed entry on the fields that must not change (COMMON_INVARIANTS + "invariants")
Env: STELLAR=<path to stellar CLI> (default: corpus/tools/get-stellar-cli.sh)
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = ROOT / "corpus" / "manifest.json"
TESTNET = "Test SDF Network ; September 2015"

# Hand-written selectors and metadata. Everything under "expected"/"extra" is computed.
ENTRIES = [
    {
        "id": "cap0040-signed-payload-signer-01",
        "title": "set_options adds an ed25519 signed-payload signer (P... strkey)",
        "cap": ["CAP-0040"],
        "sep": ["SEP-0023"],
        "introduced_in": 19,
        "dir": "corpus/entries/cap-0040/cap0040-signed-payload-signer-01",
        "ledger": 4437357,
        "tx_hash": "d9f78749cd01297cd5f22c7f2c798c9eec93d56f399e1c4cc2f8977ca52b9fed",
        "op_index": 0,
        "source_kind": "mined",
        "generator": None,
        "pattern": {
            "id": "signed-payload-signer",
            "description": "A signer key of type SIGNER_KEY_TYPE_ED25519_SIGNED_PAYLOAD: 32-byte ed25519 key plus "
            "a 1-64 byte payload, 40-100 bytes in all. Code that assumes signer keys are 32 bytes fails or truncates.",
        },
        "related": [
            {"kind": "sighting", "url": "https://github.com/stellar/wallet-backend/pull/729"},
            {"kind": "sighting", "url": "https://github.com/stellar/wallet-backend/issues/716"},
        ],
        "evidence": "sighting-729/README.md",
        "invariants": ["extra.signer_key_type", "extra.signer_weight"],
    },
    {
        "id": "cap0027-muxed-sac-destination-01",
        "title": "native SAC transfer to a muxed account ScAddress (M... recipient)",
        "cap": ["CAP-0027", "CAP-0067"],
        "sep": ["SEP-0023", "SEP-0041"],
        "introduced_in": 23,
        "dir": "corpus/entries/cap-0027/cap0027-muxed-sac-destination-01",
        "ledger": 5708671,
        "tx_hash": None,  # from generation.json
        "op_index": 0,
        "source_kind": "generated",
        "generator": "corpus/gen/run.sh muxed-soroban",
        "pattern": {
            "id": "muxed-soroban-recipient",
            "description": "A Soroban SAC transfer whose `to` is an ScAddress of type MUXED_ACCOUNT. The event topic "
            "carries the base G... account and the muxed id moves to a `to_muxed_id` data field. Readers that "
            "render the ScAddress as M... and store 32-byte keys truncate it. A muxed op or tx source on a "
            "Soroban tx is rejected by core since protocol 25 (see negative_probes).",
        },
        "related": [
            {"kind": "target", "url": "https://github.com/stellar/wallet-backend/issues/716"},
        ],
        "evidence": None,
        "invariants": ["extra.contract", "extra.function", "extra.to_scaddress_type", "extra.muxed_id", "extra.amount",
                       "extra.event_to_muxed_id", "extra.event_amount"],
    },
    {
        "id": "lp-issuer-order-01",
        "title": "change_trust on a pool share whose issuers sort differently as strkey text and as key bytes",
        "cap": ["CAP-0038"],
        "sep": [],
        "introduced_in": 18,
        "dir": "corpus/entries/cap-0038/lp-issuer-order-01",
        "ledger": 5708628,
        "tx_hash": None,  # from generation.json
        "op_index": 5,
        "source_kind": "generated",
        "generator": "corpus/gen/run.sh lp-issuer-order",
        "pattern": {
            "id": "lp-issuer-order",
            "description": "Two assets with the same code whose issuers' G... strkeys sort in the opposite order "
            "to their raw key bytes (base32 digits 2-7 encode values after A-Z but sort before them in ASCII). "
            "Core requires byte order; the text-ordered pair is rejected as malformed (see negative_probes).",
        },
        "related": [
            {"kind": "target", "url": "https://github.com/stellar/js-stellar-sdk/pull/1766"},
        ],
        "evidence": "conformance/README.md",
        "invariants": ["extra.asset_code", "extra.issuers_byte_order_a_lt_b", "extra.issuers_text_order_a_lt_b",
                       "extra.fee_bps"],
    },
    {
        "id": "cap0027-muxed-classic-payment-01",
        "title": "classic payment with a muxed transaction source, operation source and destination (three M... ids)",
        "cap": ["CAP-0027"],
        "sep": ["SEP-0023"],
        "introduced_in": 13,
        "dir": "corpus/entries/cap-0027/cap0027-muxed-classic-payment-01",
        "ledger": 5710218,
        "tx_hash": None,  # from generation.json
        "op_index": 0,
        "source_kind": "generated",
        "generator": "corpus/gen/run.sh muxed-classic",
        "pattern": {
            "id": "muxed-classic-sources",
            "description": "A classic payment whose transaction source, operation source and destination are muxed "
            "accounts with three different ids: 2^63+1, 2^53+1 (the smallest a float64 cannot hold) and 2^64-1. "
            "The tx and op sources share one base account. Readers that store 32-byte keys truncate the M... "
            "addresses; readers that keep ids as floats or confuse the three accounts are visible. This is the "
            "muxed-source shape: on Soroban it is rejected since protocol 25 (see cap0027-muxed-sac-destination-01).",
        },
        "related": [
            {"kind": "target", "url": "https://github.com/stellar/wallet-backend/issues/716"},
            {"kind": "target", "url": "https://github.com/stellar/js-stellar-sdk/issues/1785"},
        ],
        "evidence": None,
        "invariants": ["extra.tx_source_muxed_id", "extra.op_source_muxed_id", "extra.destination_muxed_id",
                       "extra.tx_and_op_source_same_base", "extra.amount"],
    },
    {
        "id": "cap0046-scmap-key-collision-01",
        "title": "contract call whose argument and return value are an ScMap with keys u32 1, string \"1\", symbol \"1\"",
        "cap": ["CAP-0046"],
        "sep": [],
        "introduced_in": 20,
        "dir": "corpus/entries/cap-0046/cap0046-scmap-key-collision-01",
        "ledger": 5710299,
        "tx_hash": None,  # from generation.json
        "op_index": 0,
        "source_kind": "generated",
        "generator": "corpus/gen/run.sh scmap-collision",
        "pattern": {
            "id": "scmap-key-collision",
            "description": "An ScMap whose keys have different ScVal types but the same text form (u32 1, string "
            "\"1\", symbol \"1\"). It is a valid map: keys are distinct and in host order (by type). Code that turns "
            "map keys into text, such as JavaScript object properties, merges them and loses entries. The map is "
            "the argument of a call to corpus/gen/contracts/map-echo, and its return value in the ledger meta.",
        },
        "related": [
            {"kind": "sighting", "url": "https://github.com/stellar/js-stellar-sdk/issues/1789"},
        ],
        "evidence": "conformance/README.md",
        "invariants": ["extra.function", "extra.arg_map_keys", "extra.arg_map_entries", "extra.keys_text_form_equal",
                       "extra.return_equals_arg"],
    },
]

# Fields that must be identical between a committed entry and a regenerated one. Everything else
# (keys, hashes, ledger numbers, apply index, ledger protocol) is expected to differ.
COMMON_INVARIANTS = [
    "synthetic", "source.kind", "pattern.id", "protocol.introduced_in", "core_validates", "roundtrip.byte_exact",
    "expected.lcm_version", "expected.tx_envelope_type", "expected.tx_result", "expected.op_index",
    "expected.op_type", "expected.op_result", "negative_probes",
]

B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def stellar_bin():
    if os.environ.get("STELLAR"):
        return os.environ["STELLAR"]
    out = subprocess.run([str(ROOT / "corpus" / "tools" / "get-stellar-cli.sh")], check=True, capture_output=True, text=True)
    return out.stdout.strip().splitlines()[-1]


STELLAR = stellar_bin()


def cli(*args, data=None):
    r = subprocess.run([STELLAR, *args], input=data, capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"stellar {' '.join(args[:4])}: {r.stderr.decode().strip()}")
    return r.stdout


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def strkey_payload(s):
    """Decode a strkey (no checksum check needed: the CLI produced it) to (version_char, payload bytes)."""
    bits = "".join(f"{B32.index(c):05b}" for c in s)
    raw = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8))
    return s[0], raw[1:-2]


def find_envelopes(node):
    if isinstance(node, dict):
        inner = node.get("tx") or node.get("tx_fee_bump") or node.get("tx_v0")
        if len(node) == 1 and isinstance(inner, dict) and "signatures" in inner:
            yield node
            return
        for v in node.values():
            yield from find_envelopes(v)
    elif isinstance(node, list):
        for v in node:
            yield from find_envelopes(v)


def envelope_hash(env, passphrase):
    """Transaction hash as core computes it: sha256(network_id || envelope type || tx XDR).

    v1 ("tx") hashes Transaction with ENVELOPE_TYPE_TX (2); fee bumps hash FeeBumpTransaction with
    ENVELOPE_TYPE_TX_FEE_BUMP (5); v0 hashes as the equivalent v1 Transaction, whose XDR is the
    TransactionV0 XDR prefixed by the 4-byte KEY_TYPE_ED25519 (0) muxed-account tag (the optional
    TimeBounds encodes like the PRECOND_NONE/PRECOND_TIME union).
    """
    kind, body = one_key(env)
    xdr_type, tag, prefix = {
        "tx": ("Transaction", 2, b""),
        "tx_fee_bump": ("FeeBumpTransaction", 5, b""),
        "tx_v0": ("TransactionV0", 2, b"\0\0\0\0"),
    }[kind]
    raw = cli("xdr", "encode", "--type", xdr_type, "--input", "json", "--output", "single",
              data=json.dumps(body["tx"]).encode())
    network_id = hashlib.sha256(passphrase.encode()).digest()
    return hashlib.sha256(network_id + tag.to_bytes(4, "big") + prefix + raw).hexdigest()


def one_key(d):
    """XDR unions decode as {"arm": value}; return (arm, value)."""
    (k, v), = d.items()
    return k, v


def rel(p):
    return os.path.relpath(p, ROOT)


def build_entry(e, entry_dir=None):
    """Build a manifest entry. entry_dir: a regenerated directory; its ledger comes from generation.json."""
    d = Path(entry_dir).resolve() if entry_dir else ROOT / e["dir"]
    gen = json.loads((d / "generation.json").read_text()) if (d / "generation.json").exists() else None
    ledger = gen["target_ledger"] if entry_dir else e["ledger"]
    tx_hash = e["tx_hash"] or gen["target_tx_hash"]
    passphrase = gen["network_passphrase"] if gen else TESTNET
    zst = next(d.glob("*.xdr.zst"))
    xdr = d / f"ledger-{ledger}.LedgerCloseMetaBatch.xdr"

    # file checks: zst -> xdr, and byte-exact round trip
    raw = subprocess.run(["zstd", "-qdc", str(zst)], check=True, capture_output=True).stdout
    if raw != xdr.read_bytes():
        raise RuntimeError(f"{zst} does not decompress to {xdr}")
    decoded = cli("xdr", "decode", "--type", "LedgerCloseMetaBatch", "--input", "single", "--output", "json", str(xdr))
    reencoded = cli("xdr", "encode", "--type", "LedgerCloseMetaBatch", "--input", "json", "--output", "single",
                    data=decoded)
    if reencoded != raw:
        raise RuntimeError(f"{xdr}: decode -> encode is not byte-exact")
    batch = json.loads(decoded)

    lcm_version, lcm = one_key(batch["ledger_close_metas"][0])
    header = lcm["ledger_header"]["header"]
    procs = lcm["tx_processing"]
    idx = next(i for i, p in enumerate(procs) if p["result"]["transaction_hash"] == tx_hash)
    proc = procs[idx]
    tx_result_code, op_results = one_key(proc["result"]["result"]["result"])
    # Hash every envelope: the set must equal the hashes in tx_processing (checks the hashing and the file).
    by_hash = {envelope_hash(x, passphrase): x for x in find_envelopes(lcm["tx_set"])}
    if set(by_hash) != {p["result"]["transaction_hash"] for p in procs}:
        raise RuntimeError(f"{e['id']}: tx_set envelope hashes do not match tx_processing hashes")
    env = by_hash[tx_hash]

    # Derived file: the target tx's envelope on its own, so an SDK that cannot decode this ledger's
    # meta (newer protocol) can still be tested on the pattern. Re-encoded from the decode; its hash is
    # the tx hash, so the bytes are the on-chain envelope.
    env_bytes = cli("xdr", "encode", "--type", "TransactionEnvelope", "--input", "json", "--output", "single",
                    data=json.dumps(env).encode())
    env_file = d / f"tx-{tx_hash[:16]}.TransactionEnvelope.xdr"
    if CHECK_ONLY:
        if not env_file.exists() or env_file.read_bytes() != env_bytes:
            raise RuntimeError(f"{rel(env_file)} is missing or differs from the envelope in the ledger")
    else:
        env_file.write_bytes(env_bytes)
    env_kind, env_body = one_key(env)
    tx = env_body["tx"]
    op = tx["operations"][e["op_index"]]
    op_type, op_body = one_key(op["body"])
    op_res_type, op_res = one_key(op_results[e["op_index"]]["op_inner"])
    op_res_code = op_res if isinstance(op_res, str) else one_key(op_res)[0]
    if tx_result_code != "tx_success":
        raise RuntimeError(f"{e['id']}: target tx did not succeed ({tx_result_code})")

    expected = {
        "lcm_version": lcm_version,
        "ledger_seq": header["ledger_seq"],
        "ledger_protocol": header["ledger_version"],
        "tx_hash": tx_hash,
        "tx_apply_index": idx,
        "tx_envelope_type": env_kind,
        "tx_source_account": tx["source_account"],
        "tx_result": tx_result_code,
        "op_index": e["op_index"],
        "op_type": op_type,
        "op_source_account": op["source_account"],
        "op_result": op_res_code,
    }
    extra, probes = EXTRA[e["id"]](op_body, proc, gen)

    entry = {
        "id": e["id"],
        "title": e["title"],
        "cap": e["cap"],
        "sep": e["sep"],
        "protocol": {"introduced_in": e["introduced_in"], "ledger": header["ledger_version"]},
        "network": "testnet" if passphrase == TESTNET else "other",
        "network_passphrase": passphrase,
        "source": {
            "kind": e["source_kind"],
            "ledger": ledger,
            "tx_hash": tx_hash,
            "generator": e["generator"],
            "generation_record": rel(d / "generation.json") if gen else None,
        },
        "synthetic": False,
        "pattern": e["pattern"],
        "files": [
            {"path": rel(zst), "xdr_type": "LedgerCloseMetaBatch", "encoding": "xdr+zstd",
             "layout": "galexie", "sha256": sha256(zst)},
            {"path": rel(xdr), "xdr_type": "LedgerCloseMetaBatch", "encoding": "xdr",
             "sha256": sha256(xdr)},
            {"path": rel(env_file), "xdr_type": "TransactionEnvelope", "encoding": "xdr",
             "sha256": sha256(env_file), "derived_from": rel(xdr)},
        ],
        "core_validates": True,
        "negative_probes": probes,
        "roundtrip": {"byte_exact": True, "tool": cli("--version").decode().splitlines()[1].strip()},
        "check_lcm_golden": None,
        "expected": expected,
        "extra": extra,
        "related": e["related"],
        "evidence": e["evidence"],
    }
    return entry


def extra_signed_payload(op_body, proc, gen):
    signer = op_body["signer"]
    key = signer["key"]
    _, payload = strkey_payload(key)
    ed25519, n = payload[:32], int.from_bytes(payload[32:36], "big")
    return {
        "signer_key": key,
        "signer_key_type": "SIGNER_KEY_TYPE_ED25519_SIGNED_PAYLOAD",
        "signer_key_payload_bytes": len(payload),
        "signed_payload_hex": payload[36 : 36 + n].hex(),
        "signed_payload_len": n,
        "signer_ed25519_hex": ed25519.hex(),
        "signer_weight": signer["weight"],
    }, []


def extra_muxed_sac(op_body, proc, gen):
    args = op_body["host_function"]["invoke_contract"]["args"]
    to = args[1]["address"]
    _, payload = strkey_payload(to)
    evs = proc["tx_apply_processing"]["v4"]["operations"][0]["events"]
    transfer = next(ev for ev in evs if ev["body"]["v0"]["topics"][0] == {"symbol": "transfer"})
    data = {kv["key"]["symbol"]: one_key(kv["val"])[1] for kv in transfer["body"]["v0"]["data"]["map"]}
    probes = [
        {"variant": s["purpose"].removeprefix("target: "), "stage": "submission", "result": decode_result(s),
         "diagnostic": [decode_diag(x) for x in s.get("diagnostic_events_xdr", [])]}
        for s in gen["submissions"] if not s["accepted"]
    ]
    return {
        "contract": op_body["host_function"]["invoke_contract"]["contract_address"],
        "function": op_body["host_function"]["invoke_contract"]["function_name"],
        "from": args[0]["address"],
        "to_scaddress": to,
        "to_scaddress_type": "SC_ADDRESS_TYPE_MUXED_ACCOUNT",
        # SEP-23 muxed payload: 32-byte ed25519 key, then the 8-byte big-endian id
        "to_base_account_ed25519_hex": payload[:32].hex(),
        "muxed_id": str(int.from_bytes(payload[32:40], "big")),
        "amount": args[2]["i128"],
        "event_topic_to": transfer["body"]["v0"]["topics"][2]["address"],
        "event_to_muxed_id": data.get("to_muxed_id"),
        "event_amount": data.get("amount"),
    }, probes


def extra_lp(op_body, proc, gen):
    params = op_body["line"]["pool_share"]["liquidity_pool_constant_product"]
    a = one_key(params["asset_a"])[1]
    b = one_key(params["asset_b"])[1]
    ka, kb = strkey_payload(a["issuer"])[1], strkey_payload(b["issuer"])[1]
    created = [one_key(c)[1] for c in proc["tx_apply_processing"]["v4"]["operations"][5]["changes"]]
    pool = next(c["data"]["liquidity_pool"] for c in created if isinstance(c, dict) and "liquidity_pool" in c.get("data", {}))
    probes = [
        {"variant": s["purpose"].removeprefix("probe: "), "stage": "submission", "result": decode_result(s),
         "diagnostic": [decode_diag(x) for x in s.get("diagnostic_events_xdr", [])]}
        for s in gen["submissions"] if not s["accepted"]
    ]
    return {
        "asset_code": a["asset_code"],
        "asset_a_issuer": a["issuer"],
        "asset_b_issuer": b["issuer"],
        "issuers_byte_order_a_lt_b": ka < kb,
        "issuers_text_order_a_lt_b": a["issuer"] < b["issuer"],
        "fee_bps": params["fee"],
        "pool_id": pool["liquidity_pool_id"],
        "pool_id_hex": strkey_payload(pool["liquidity_pool_id"])[1].hex(),
    }, probes


def crc16_xmodem(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def strkey_encode(version_char, payload):
    """SEP-23 strkey: base32(version byte || payload || crc16-xmodem little-endian), no padding."""
    raw = bytes([B32.index(version_char) << 3]) + payload
    raw += crc16_xmodem(raw).to_bytes(2, "little")
    bits = "".join(f"{b:08b}" for b in raw)
    bits += "0" * (-len(bits) % 5)
    return "".join(B32[int(bits[i : i + 5], 2)] for i in range(0, len(bits), 5))


def muxed_parts(m):
    """M... strkey -> (base G... account, id as decimal string)."""
    _, payload = strkey_payload(m)
    return strkey_encode("G", payload[:32]), str(int.from_bytes(payload[32:40], "big"))


def extra_muxed_classic(op_body, proc, gen):
    rec = {k: gen[k] for k in ("tx_source_muxed", "op_source_muxed", "destination_muxed")}
    out = {}
    for name, m in rec.items():
        base, mid = muxed_parts(m)
        if strkey_encode("M", strkey_payload(m)[1]) != m:
            raise RuntimeError(f"strkey round trip failed for {m}")
        prefix = name.removesuffix("_muxed")
        out[f"{prefix}_muxed"] = m
        out[f"{prefix}_base"] = base
        out[f"{prefix}_muxed_id"] = mid
    out["tx_and_op_source_same_base"] = out["tx_source_base"] == out["op_source_base"]
    out["amount"] = op_body["amount"]
    out["asset"] = op_body["asset"] if isinstance(op_body["asset"], str) else one_key(op_body["asset"])[0]
    return out, []


def scval_key(v):
    """One ScVal map key as {"type": ..., "value": ...} (scalar keys only, which is what this pattern uses)."""
    t, val = one_key(v)
    return {"type": t, "value": val}


def extra_scmap(op_body, proc, gen):
    ic = op_body["host_function"]["invoke_contract"]
    arg = ic["args"][0]["map"]
    ret = proc["tx_apply_processing"]["v4"]["soroban_meta"]["return_value"]["map"]
    keys = [scval_key(e["key"]) for e in arg]
    return {
        "contract": ic["contract_address"],
        "function": ic["function_name"],
        "arg_map_entries": len(arg),
        "arg_map_keys": keys,
        "keys_text_form_equal": len({str(k["value"]) for k in keys}) == 1,
        "return_map_entries": len(ret),
        "return_equals_arg": ret == arg,
        "wasm_sha256": gen["wasm_sha256"],
    }, []


def decode_result(sub):
    res = json.loads(cli("xdr", "decode", "--type", "TransactionResult", "--output", "json",
                         data=sub["error_result_xdr"].encode()))
    r = res["result"]
    if isinstance(r, str):
        return r
    code, ops = one_key(r)
    failing = [(i, one_key(o["op_inner"])) for i, o in enumerate(ops) if isinstance(o, dict) and "op_inner" in o]
    bad = [f"op {i} {t}: {v if isinstance(v, str) else one_key(v)[0]}" for i, (t, v) in failing
           if (v if isinstance(v, str) else one_key(v)[0]) != "success"]
    return code + (" (" + "; ".join(bad) + ")" if bad else "")


def decode_diag(b64):
    ev = json.loads(cli("xdr", "decode", "--type", "DiagnosticEvent", "--output", "json", data=b64.encode()))
    data = ev["event"]["body"]["v0"]["data"]
    return data.get("string", json.dumps(data))


EXTRA = {
    "cap0040-signed-payload-signer-01": extra_signed_payload,
    "cap0027-muxed-sac-destination-01": extra_muxed_sac,
    "lp-issuer-order-01": extra_lp,
    "cap0027-muxed-classic-payment-01": extra_muxed_classic,
    "cap0046-scmap-key-collision-01": extra_scmap,
}


def get_path(obj, path):
    for part in path.split("."):
        obj = obj[part]
    return obj


def regen_check(entry_id, entry_dir):
    e = next((x for x in ENTRIES if x["id"] == entry_id), None)
    if e is None:
        sys.exit(f"unknown entry {entry_id}")
    committed = next(x for x in json.loads(MANIFEST.read_text())["entries"] if x["id"] == entry_id)
    fresh = build_entry(e, entry_dir)
    (Path(entry_dir) / "manifest-entry.json").write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n")
    ok = True
    print(f"{'field':40} {'committed':>24}   regenerated")
    for path in COMMON_INVARIANTS + e["invariants"]:
        a, b = get_path(committed, path), get_path(fresh, path)
        same = a == b
        ok &= same
        show = lambda v: (json.dumps(v)[:60] + "...") if len(json.dumps(v)) > 63 else json.dumps(v)
        print(f"{'ok  ' if same else 'DIFF'} {path:35} {show(a):>24}   {'(same)' if same else show(b)}")
    changed = [k for k in sorted(set(committed["extra"]) | set(fresh["extra"])) if k not in committed["extra"] or k not in fresh["extra"]]
    if changed:
        ok = False
        print(f"DIFF extra keys differ: {changed}")
    print(f"\nregenerated: ledger {fresh['source']['ledger']}, tx {fresh['source']['tx_hash']}")
    print("SAME PATTERN AND CORE VERDICTS" if ok else "MISMATCH", "-", entry_id)
    sys.exit(0 if ok else 1)


CHECK_ONLY = "--check" in sys.argv


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--regen-check":
        regen_check(sys.argv[2], sys.argv[3])
        return
    manifest = {
        "$schema": "./manifest.schema.json",
        "manifest_version": "0.1.0-draft",
        "generated_by": "corpus/tools/build-manifest.py",
        "entries": [build_entry(e) for e in ENTRIES],
    }
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not MANIFEST.exists() or MANIFEST.read_text() != text:
            print("corpus/manifest.json is out of date; run corpus/tools/build-manifest.py", file=sys.stderr)
            sys.exit(1)
        print(f"manifest up to date: {len(manifest['entries'])} entries, all round-trip byte-exact")
        return
    MANIFEST.write_text(text)
    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(manifest['entries'])} entries")


if __name__ == "__main__":
    main()
