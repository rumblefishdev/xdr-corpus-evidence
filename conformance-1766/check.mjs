// Run corpus entry lp-issuer-order-01 through @stellar/stellar-sdk and report a verdict.
// Usage (inside a dir with the SDK installed): node check.mjs <repo root> <result.json>
//
// Inputs come from the corpus: the target tx envelope file and the manifest's expected values.
// Nothing about the pool is typed here.
//
// Verdict checks, each compared with what the network did:
//   1. XDR layer: decode the TransactionEnvelope and re-encode it byte-exactly;
//   2. TransactionBuilder.fromXDR parses the on-chain tx, its hash is the manifest's, and the change_trust op's
//      pool share is the manifest's pair in network order;
//   3. Asset.compare(A, B) < 0, as on the network (key-byte order);
//   4. new LiquidityPoolAsset(A, B, fee) accepts the pair the network accepted;
//   5. getLiquidityPoolId(A, B, fee) equals the pool id the network computed (manifest extra.pool_id_hex).
// Checks 3-5 build A and B from the manifest's decoded issuers, so they run even if 2 fails.
// Verdict: pass (all ok) | mishandled (XDR decodes, but a check failed) | error (envelope not decodable).
//
// Reported but not part of the verdict: whether this SDK can decode the whole protocol-30 ledger file
// (17.2.x can't; 17.3.0+ can).
import fs from "node:fs";
import path from "node:path";
import * as sdk from "@stellar/stellar-sdk";

const { xdr, Asset, LiquidityPoolAsset, getLiquidityPoolId, TransactionBuilder } = sdk;
const [ROOT, OUT] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "corpus/manifest.json")));
const entry = manifest.entries.find((e) => e.id === "lp-issuer-order-01");
const fileOf = (type) => path.join(ROOT, entry.files.find((f) => f.xdr_type === type && f.encoding === "xdr").path);
const version = JSON.parse(fs.readFileSync("node_modules/@stellar/stellar-sdk/package.json")).version;

const run = (name, fn) => {
  try {
    return { name, ok: true, detail: fn() };
  } catch (err) {
    return { name, ok: false, detail: String(err.message || err) };
  }
};

// Informational: full ledger decode.
const ledger = run("decode + re-encode the protocol-30 LedgerCloseMetaBatch", () => {
  const raw = fs.readFileSync(fileOf("LedgerCloseMetaBatch"));
  const batch = xdr.LedgerCloseMetaBatch.fromXDR(raw);
  if (!Buffer.from(batch.toXDR()).equals(raw)) throw new Error("re-encoded bytes differ");
  return `${raw.length} bytes, byte-exact`;
});

// Verdict checks.
const checks = [];
const envFile = fileOf("TransactionEnvelope");
const xdrCheck = run("XDR: decode + re-encode the target TransactionEnvelope byte-exact", () => {
  const raw = fs.readFileSync(envFile);
  if (!Buffer.from(xdr.TransactionEnvelope.fromXDR(raw).toXDR()).equals(raw)) throw new Error("re-encoded bytes differ");
  return `${raw.length} bytes`;
});
checks.push(xdrCheck);

checks.push(run("TransactionBuilder.fromXDR parses the on-chain tx; hash and op 5 pool pair match", () => {
  const tx = TransactionBuilder.fromXDR(fs.readFileSync(envFile).toString("base64"), entry.network_passphrase);
  const hash = Buffer.from(tx.hash()).toString("hex");
  if (hash !== entry.expected.tx_hash) throw new Error(`hash ${hash} != ${entry.expected.tx_hash}`);
  const line = tx.operations[entry.expected.op_index].line;
  if (line.assetA.getIssuer() !== entry.extra.asset_a_issuer || line.assetB.getIssuer() !== entry.extra.asset_b_issuer)
    throw new Error("pool pair differs from the manifest");
  return `hash ok, op ${entry.expected.op_index} = change_trust on ${line.assetA.getCode()} pool, pair matches`;
}));

// The pair as the network ordered it, built from the manifest's decoded values (independent of the parse above).
const A = new Asset(entry.extra.asset_code, entry.extra.asset_a_issuer);
const B = new Asset(entry.extra.asset_code, entry.extra.asset_b_issuer);
const fee = entry.extra.fee_bps;
checks.push(run("Asset.compare(A, B) < 0, as on the network", () => {
  const c = Asset.compare(A, B);
  if (!(c < 0)) throw new Error(`Asset.compare returned ${c}`);
  return `returned ${c}`;
}));
checks.push(run("new LiquidityPoolAsset(A, B, fee) accepts the network's pair", () => {
  new LiquidityPoolAsset(A, B, fee);
  return "accepted";
}));
checks.push(run("getLiquidityPoolId(A, B, fee) = the network's pool id", () => {
  const id = Buffer.from(getLiquidityPoolId("constant_product", { assetA: A, assetB: B, fee })).toString("hex");
  if (id !== entry.extra.pool_id_hex) throw new Error(`got ${id}, network ${entry.extra.pool_id_hex}`);
  return id;
}));

const verdict = !xdrCheck.ok ? "error" : checks.every((c) => c.ok) ? "pass" : "mishandled";
console.log(`@stellar/stellar-sdk ${version}, entry ${entry.id}`);
for (const c of checks) console.log(`  ${c.ok ? "ok  " : "FAIL"} ${c.name}: ${c.detail}`);
console.log(`  info ${ledger.name}: ${ledger.ok ? "ok, " : "fails: "}${ledger.detail}`);
console.log(`  verdict: ${verdict}`);
fs.writeFileSync(OUT, JSON.stringify({ sdk: "@stellar/stellar-sdk", version, entry: entry.id, verdict, checks, info: [ledger] }, null, 2) + "\n");
