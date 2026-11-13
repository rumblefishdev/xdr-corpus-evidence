#!/usr/bin/env python3
"""Build corpus/manifest.json from the corpus files.

Every expected value is derived from `stellar xdr decode` of the committed ledger files.
The only hand-written inputs are in ENTRIES below: which tx and operation an entry is about
(the selector), CAP/SEP tags, the protocol a CAP was introduced in, and issue links.

For each entry the script also checks:
  * the .xdr.zst file decompresses to the committed .xdr file,
  * decode -> encode of the .xdr file is byte-exact,
  * the target tx is in the ledger and succeeded.

Usage: scripts/build-manifest.py [--check]
  --check  do not write; exit 1 if corpus/manifest.json differs from what would be written
Env: STELLAR=<path to stellar CLI> (default: scripts/get-stellar-cli.sh)
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
        "dir": "corpus/cap-0040/cap0040-signed-payload-signer-01",
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
        "evidence": "evidence/729/README.md",
    },
    {
        "id": "cap0027-muxed-sac-destination-01",
        "title": "native SAC transfer to a muxed account ScAddress (M... recipient)",
        "cap": ["CAP-0027", "CAP-0067"],
        "sep": ["SEP-0023", "SEP-0041"],
        "introduced_in": 23,
        "dir": "corpus/cap-0027/cap0027-muxed-sac-destination-01",
        "ledger": 5708671,
        "tx_hash": None,  # from generation.json
        "op_index": 0,
        "source_kind": "generated",
        "generator": "gen/run.sh muxed-soroban",
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
    },
    {
        "id": "lp-issuer-order-01",
        "title": "change_trust on a pool share whose issuers sort differently as strkey text and as key bytes",
        "cap": ["CAP-0038"],
        "sep": [],
        "introduced_in": 18,
        "dir": "corpus/cap-0038/lp-issuer-order-01",
        "ledger": 5708628,
        "tx_hash": None,  # from generation.json
        "op_index": 5,
        "source_kind": "generated",
        "generator": "gen/run.sh lp-issuer-order",
        "pattern": {
            "id": "lp-issuer-order",
            "description": "Two assets with the same code whose issuers' G... strkeys sort in the opposite order "
            "to their raw key bytes (base32 digits 2-7 encode values after A-Z but sort before them in ASCII). "
            "Core requires byte order; the text-ordered pair is rejected as malformed (see negative_probes).",
        },
        "related": [
            {"kind": "target", "url": "https://github.com/stellar/js-stellar-sdk/pull/1766"},
        ],
        "evidence": None,
    },
]

B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def stellar_bin():
    if os.environ.get("STELLAR"):
        return os.environ["STELLAR"]
    out = subprocess.run([str(ROOT / "scripts" / "get-stellar-cli.sh")], check=True, capture_output=True, text=True)
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
    b64 = cli("xdr", "encode", "--type", "TransactionEnvelope", "--input", "json", "--output", "single-base64",
              data=json.dumps(env).encode()).decode().strip()
    return cli("tx", "hash", "--network-passphrase", passphrase, b64).decode().strip()


def one_key(d):
    """XDR unions decode as {"arm": value}; return (arm, value)."""
    (k, v), = d.items()
    return k, v


def build_entry(e):
    d = ROOT / e["dir"]
    gen = json.loads((d / "generation.json").read_text()) if (d / "generation.json").exists() else None
    tx_hash = e["tx_hash"] or gen["target_tx_hash"]
    passphrase = gen["network_passphrase"] if gen else TESTNET
    zst = next(d.glob("*.xdr.zst"))
    xdr = d / f"ledger-{e['ledger']}.LedgerCloseMetaBatch.xdr"

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
    env = next(x for x in find_envelopes(lcm["tx_set"]) if envelope_hash(x, passphrase) == tx_hash)
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
            "ledger": e["ledger"],
            "tx_hash": tx_hash,
            "generator": e["generator"],
            "generation_record": str((d / "generation.json").relative_to(ROOT)) if gen else None,
        },
        "synthetic": False,
        "pattern": e["pattern"],
        "files": [
            {"path": str(zst.relative_to(ROOT)), "xdr_type": "LedgerCloseMetaBatch", "encoding": "xdr+zstd",
             "layout": "galexie", "sha256": sha256(zst)},
            {"path": str(xdr.relative_to(ROOT)), "xdr_type": "LedgerCloseMetaBatch", "encoding": "xdr",
             "sha256": sha256(xdr)},
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
}


def main():
    manifest = {
        "$schema": "./manifest.schema.json",
        "manifest_version": "0.1.0-draft",
        "generated_by": "scripts/build-manifest.py",
        "entries": [build_entry(e) for e in ENTRIES],
    }
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not MANIFEST.exists() or MANIFEST.read_text() != text:
            print("corpus/manifest.json is out of date; run scripts/build-manifest.py", file=sys.stderr)
            sys.exit(1)
        print(f"manifest up to date: {len(manifest['entries'])} entries, all round-trip byte-exact")
        return
    MANIFEST.write_text(text)
    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(manifest['entries'])} entries")


if __name__ == "__main__":
    main()
