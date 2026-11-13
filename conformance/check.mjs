// Run every corpus entry through one version of @stellar/stellar-sdk and report a verdict per entry.
// Usage (inside a dir with the SDK installed): node check.mjs <repo root> <result.json>
//
// The checks are GENERIC: they are the same for every entry and know nothing about any particular bug.
// Inputs come only from the corpus (the entries' envelope and ledger files, and the manifest).
//
//   1. xdr-roundtrip   decode the target TransactionEnvelope and re-encode it; the bytes must be identical.
//   2. tx-roundtrip    TransactionBuilder.fromXDR parses the envelope; its hash equals the manifest's tx hash;
//                      toEnvelope() re-encodes to the identical bytes. (What a wallet or indexer does to load a tx.)
//   3. scval-maps      every scvMap in the envelope goes through scValToNative: the result must keep every entry,
//                      or the SDK must fail with an error. Silently returning fewer entries is a failure.
//   4. muxed           every muxed account (M...) in the envelope, as a MuxedAccount or an ScAddress, renders to an
//                      address that decodes back to the same bytes and the same id.
// Verdict per entry: pass (all ok) | mishandled (envelope decodes, a later check fails) | error (check 1 fails).
// Reported but not part of the verdict: whether this SDK decodes the entry's full ledger file (ledger-meta support
// can lag behind transaction support).
import fs from "node:fs";
import path from "node:path";
import * as sdk from "@stellar/stellar-sdk";

const { xdr, TransactionBuilder, scValToNative } = sdk;
const [ROOT, OUT] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "corpus/manifest.json")));
const version = JSON.parse(fs.readFileSync("node_modules/@stellar/stellar-sdk/package.json")).version;

const run = (name, fn) => {
  try {
    return { name, ok: true, detail: fn() };
  } catch (err) {
    return { name, ok: false, detail: String(err.message || err).split("\n")[0] };
  }
};

// Visit every XDR value inside v. Works for both object models the SDK has used: SDK 17 decodes to objects
// whose fields are own properties ({type: "scvMap", map: [...]}); older js-xdr kept struct fields in
// _attributes and union arms in _value. Recursing into every own property covers both.
function walk(v, visit, seen = new Set()) {
  if (v === null || typeof v !== "object" || seen.has(v) || Buffer.isBuffer(v) || v instanceof Uint8Array) return;
  seen.add(v);
  visit(v);
  for (const x of Array.isArray(v) ? v : Object.values(v)) walk(x, visit, seen);
}
const kind = (v) => v.type ?? (typeof v.switch === "function" ? v.switch().name : undefined);
const field = (v, name) => (typeof v[name] === "function" ? v[name]() : v[name]);

function checkMaps(root) {
  const notes = [];
  let n = 0;
  walk(root, (v) => {
    if (!(v instanceof xdr.ScVal) || kind(v) !== "scvMap") return;
    n++;
    const want = (field(v, "map") || []).length;
    let got;
    try {
      got = Object.keys(scValToNative(v)).length;
    } catch (err) {
      notes.push(`map of ${want}: rejected with an error (${String(err.message).slice(0, 60)})`);
      return;
    }
    if (got !== want) throw new Error(`scValToNative returned ${got} of ${want} map entries, with no error`);
    notes.push(`map of ${want}: all kept`);
  });
  return n ? notes.join("; ") : "no maps";
}

function checkMuxed(root) {
  let n = 0;
  const bytes = (v) => Buffer.from(v.toXDR());
  walk(root, (v) => {
    if (kind(v) === "keyTypeMuxedEd25519" && field(v, "med25519")) {
      n++;
      const addr = sdk.encodeMuxedAccountToAddress(v);
      const back = sdk.decodeAddressToMuxedAccount(addr);
      if (!bytes(back).equals(bytes(v))) throw new Error(`${addr} decodes to different bytes`);
      const id = field(field(v, "med25519"), "id").toString();
      const parsed = sdk.MuxedAccount.fromAddress(addr, "0").id();
      if (parsed !== id) throw new Error(`${addr}: id ${parsed}, XDR has ${id}`);
    } else if (kind(v) === "scAddressTypeMuxedAccount") {
      n++;
      const a = sdk.Address.fromScAddress(v);
      if (!bytes(a.toScAddress()).equals(bytes(v))) throw new Error(`${a.toString()} re-encodes differently`);
    }
  });
  return n ? `${n} muxed address(es) round-trip` : "no muxed addresses";
}

const results = manifest.entries.map((entry) => {
  const fileOf = (type) => path.join(ROOT, entry.files.find((f) => f.xdr_type === type && f.encoding === "xdr").path);
  const raw = fs.readFileSync(fileOf("TransactionEnvelope"));
  let env;
  const checks = [
    run("xdr-roundtrip", () => {
      env = xdr.TransactionEnvelope.fromXDR(raw);
      if (!Buffer.from(env.toXDR()).equals(raw)) throw new Error("re-encoded bytes differ");
      return `${raw.length} bytes`;
    }),
  ];
  if (checks[0].ok) {
    checks.push(run("tx-roundtrip", () => {
      const tx = TransactionBuilder.fromXDR(raw.toString("base64"), entry.network_passphrase);
      const hash = Buffer.from(tx.hash()).toString("hex");
      if (hash !== entry.expected.tx_hash) throw new Error(`hash ${hash} != ${entry.expected.tx_hash}`);
      if (!Buffer.from(tx.toEnvelope().toXDR()).equals(raw)) throw new Error("toEnvelope() re-encodes differently");
      return "parsed, hash ok, re-encodes identically";
    }));
    checks.push(run("scval-maps", () => checkMaps(env)));
    checks.push(run("muxed", () => checkMuxed(env)));
  }
  const ledger = run("ledger-decode (info)", () => {
    const lraw = fs.readFileSync(fileOf("LedgerCloseMetaBatch"));
    const batch = xdr.LedgerCloseMetaBatch.fromXDR(lraw);
    if (!Buffer.from(batch.toXDR()).equals(lraw)) throw new Error("re-encoded bytes differ");
    return `protocol ${entry.expected.ledger_protocol} ledger, byte-exact`;
  });
  const verdict = !checks[0].ok ? "error" : checks.every((c) => c.ok) ? "pass" : "mishandled";
  return { entry: entry.id, verdict, checks, info: [ledger] };
});

console.log(`@stellar/stellar-sdk ${version}`);
for (const r of results) {
  console.log(`  ${r.verdict.padEnd(10)} ${r.entry}`);
  for (const c of r.checks) if (!c.ok) console.log(`             FAIL ${c.name}: ${c.detail}`);
  for (const c of r.checks) if (c.ok && c.name === "scval-maps" && c.detail !== "no maps") console.log(`             ok   ${c.name}: ${c.detail}`);
}
const ledgerOk = results.filter((r) => r.info[0].ok).length;
console.log(`  info: decodes ${ledgerOk} of ${results.length} entries' full ledger files`);
fs.writeFileSync(OUT, JSON.stringify({ sdk: "@stellar/stellar-sdk", version, results }, null, 2) + "\n");
