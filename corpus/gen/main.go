// Command gen submits the transactions behind the generated corpus entries and prints a JSON
// record of what happened (ledger, tx hash, public keys, rejections). It never prints secrets:
// every keypair is random and lives only in memory.
//
// Usage:
//
//	gen muxed-soroban     # cap0027-muxed-sac-destination-01 (target: wallet-backend #716)
//	gen lp-issuer-order   # lp-issuer-order-01 (target: js-stellar-sdk #1766)
//	gen muxed-classic     # cap0027-muxed-classic-payment-01 (muxed tx source, op source and destination)
//	gen scmap-collision   # cap0046-scmap-key-collision-01 (target: js-stellar-sdk #1789)
//
// Environment (defaults: testnet):
//
//	RPC_URL             https://soroban-testnet.stellar.org
//	NETWORK_PASSPHRASE  Test SDF Network ; September 2015
//	FRIENDBOT_URL       https://friendbot.stellar.org
//
// For a local quickstart: RPC_URL=http://localhost:8000/rpc FRIENDBOT_URL=http://localhost:8000/friendbot
// NETWORK_PASSPHRASE="Standalone Network ; February 2017".
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/stellar/go-stellar-sdk/clients/rpcclient"
	"github.com/stellar/go-stellar-sdk/keypair"
	"github.com/stellar/go-stellar-sdk/network"
	protocol "github.com/stellar/go-stellar-sdk/protocols/rpc"
	"github.com/stellar/go-stellar-sdk/strkey"
	"github.com/stellar/go-stellar-sdk/txnbuild"
	"github.com/stellar/go-stellar-sdk/xdr"
)

// muxedID is the M... id used as operation source. It is above math.MaxInt64 on purpose, so a
// consumer that stores the id in a signed 64-bit column fails visibly too.
const muxedID uint64 = 0x8000000000000001

type env struct {
	ctx        context.Context
	rpc        *rpcclient.Client
	passphrase string
	friendbot  string
}

// Submission is the JSON record of one submitted transaction.
type Submission struct {
	Purpose     string `json:"purpose"`
	Hash        string `json:"tx_hash"`
	Accepted    bool   `json:"accepted"` // reached a ledger (success or failed result)
	Status      string `json:"status"`   // RPC status: SUCCESS, FAILED, or the sendTransaction status
	Ledger      uint32 `json:"ledger,omitempty"`
	ErrorResult string `json:"error_result_xdr,omitempty"` // base64 TransactionResult if rejected/failed
	// DiagnosticEvents are the base64 DiagnosticEvents core returned with a rejection.
	DiagnosticEvents []string `json:"diagnostic_events_xdr,omitempty"`
	Error            string   `json:"error,omitempty"`
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: gen muxed-soroban|lp-issuer-order|muxed-classic|scmap-collision")
		os.Exit(2)
	}
	e := &env{
		ctx:        context.Background(),
		rpc:        rpcclient.NewClient(getenv("RPC_URL", "https://soroban-testnet.stellar.org"), nil),
		passphrase: getenv("NETWORK_PASSPHRASE", network.TestNetworkPassphrase),
		friendbot:  getenv("FRIENDBOT_URL", "https://friendbot.stellar.org"),
	}
	var (
		out any
		err error
	)
	switch os.Args[1] {
	case "muxed-soroban":
		out, err = e.muxedSoroban()
	case "lp-issuer-order":
		out, err = e.lpIssuerOrder()
	case "muxed-classic":
		out, err = e.muxedClassic()
	case "scmap-collision":
		out, err = e.scmapCollision()
	default:
		err = fmt.Errorf("unknown generator %q", os.Args[1])
	}
	if out != nil {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(out)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func logf(format string, args ...any) { fmt.Fprintf(os.Stderr, format+"\n", args...) }

// fundWithFriendbot creates and funds a new random account.
func (e *env) fundWithFriendbot() (*keypair.Full, error) {
	kp := keypair.MustRandom()
	for attempt := 1; ; attempt++ {
		resp, err := http.Get(e.friendbot + "?addr=" + url.QueryEscape(kp.Address()))
		if err == nil {
			body, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				logf("funded %s via friendbot", kp.Address())
				return kp, nil
			}
			err = fmt.Errorf("friendbot %d: %s", resp.StatusCode, bytes.TrimSpace(body))
		}
		if attempt == 5 {
			return nil, err
		}
		logf("friendbot attempt %d failed: %v", attempt, err)
		time.Sleep(time.Duration(attempt) * 3 * time.Second)
	}
}

func (e *env) loadAccount(addr string) (*txnbuild.SimpleAccount, error) {
	acc, err := e.rpc.LoadAccount(e.ctx, addr)
	if err != nil {
		return nil, fmt.Errorf("loading %s: %w", addr, err)
	}
	seq, err := acc.GetSequenceNumber()
	if err != nil {
		return nil, err
	}
	return &txnbuild.SimpleAccount{AccountID: addr, Sequence: seq}, nil
}

// submitEnvelope sends a signed envelope and waits for it to reach a ledger.
// A rejection by core is not an error: it is recorded in the Submission.
func (e *env) submitEnvelope(purpose string, envB64 string, hash [32]byte) (Submission, error) {
	s := Submission{Purpose: purpose, Hash: fmt.Sprintf("%x", hash)}
	send, err := e.rpc.SendTransaction(e.ctx, protocol.SendTransactionRequest{Transaction: envB64})
	if err != nil {
		return s, fmt.Errorf("%s: sendTransaction: %w", purpose, err)
	}
	s.Status = send.Status
	if send.Status != "PENDING" && send.Status != "DUPLICATE" {
		s.ErrorResult = send.ErrorResultXDR
		s.DiagnosticEvents = send.DiagnosticEventsXDR
		logf("%s: rejected by core at submission (%s)", purpose, send.Status)
		return s, nil
	}
	got, err := e.rpc.PollTransaction(e.ctx, s.Hash)
	if err != nil {
		return s, fmt.Errorf("%s: polling %s: %w", purpose, s.Hash, err)
	}
	s.Status, s.Ledger, s.Accepted = got.Status, got.Ledger, true
	if got.Status != protocol.TransactionStatusSuccess {
		s.ErrorResult = got.ResultXDR
	}
	logf("%s: %s in ledger %d (tx %s)", purpose, got.Status, got.Ledger, s.Hash)
	return s, nil
}

func (e *env) submit(purpose string, tx *txnbuild.Transaction, signers ...*keypair.Full) (Submission, error) {
	tx, err := tx.Sign(e.passphrase, signers...)
	if err != nil {
		return Submission{Purpose: purpose}, err
	}
	b64, err := tx.Base64()
	if err != nil {
		return Submission{Purpose: purpose}, err
	}
	hash, err := tx.Hash(e.passphrase)
	if err != nil {
		return Submission{Purpose: purpose}, err
	}
	return e.submitEnvelope(purpose, b64, hash)
}

func newTx(src *txnbuild.SimpleAccount, baseFee int64, ops ...txnbuild.Operation) (*txnbuild.Transaction, error) {
	return txnbuild.NewTransaction(txnbuild.TransactionParams{
		SourceAccount:        src,
		IncrementSequenceNum: true,
		BaseFee:              baseFee,
		Operations:           ops,
		Preconditions:        txnbuild.Preconditions{TimeBounds: txnbuild.NewTimeout(300)},
	})
}

func mustSucceed(s Submission, err error) (Submission, error) {
	if err != nil {
		return s, err
	}
	if s.Status != protocol.TransactionStatusSuccess {
		return s, fmt.Errorf("%s: expected SUCCESS, got %s (%s)", s.Purpose, s.Status, s.ErrorResult)
	}
	return s, nil
}

// ---------------------------------------------------------------------------------------------
// cap0027-muxed-sac-destination-01: Soroban + muxed accounts. Tries an invoke_host_function with a
// muxed op source, then a muxed tx source (both rejected by core since protocol 25 and recorded),
// then a native SAC transfer to a muxed ScAddress (CAP-67). Last resort: a classic payment with a
// muxed source and muxed destination.

type MuxedSorobanRecord struct {
	Entry          string       `json:"entry"`
	Network        string       `json:"network_passphrase"`
	From           string       `json:"from"`
	To             string       `json:"to"`
	MuxedID        string       `json:"muxed_id"`
	MuxedAddress   string       `json:"muxed_address"`
	NativeSAC      string       `json:"native_sac"`
	Submissions    []Submission `json:"submissions"`
	TargetTx       string       `json:"target_tx_hash,omitempty"`
	TargetLedger   uint32       `json:"target_ledger,omitempty"`
	TargetPattern  string       `json:"target_pattern"`
	FallbackReason string       `json:"fallback_reason,omitempty"`
}

func (e *env) muxedSoroban() (*MuxedSorobanRecord, error) {
	from, err := e.fundWithFriendbot()
	if err != nil {
		return nil, err
	}
	rec := &MuxedSorobanRecord{Entry: "cap0027-muxed-sac-destination-01", Network: e.passphrase, From: from.Address(), MuxedID: fmt.Sprint(muxedID)}
	muxed, err := xdr.MuxedAccountFromAccountId(from.Address(), muxedID)
	if err != nil {
		return rec, err
	}
	rec.MuxedAddress = muxed.Address()

	// Destination account, created by `from` (one friendbot call per run).
	to := keypair.MustRandom()
	rec.To = to.Address()
	src, err := e.loadAccount(from.Address())
	if err != nil {
		return rec, err
	}
	tx, err := newTx(src, txnbuild.MinBaseFee, &txnbuild.CreateAccount{Destination: to.Address(), Amount: "10"})
	if err != nil {
		return rec, err
	}
	s, err := mustSucceed(e.submit("setup: create destination", tx, from))
	rec.Submissions = append(rec.Submissions, s)
	if err != nil {
		return rec, err
	}

	// Native SAC transfer(from, to, 1 XLM).
	sacID, err := xdr.MustNewNativeAsset().ContractID(e.passphrase)
	if err != nil {
		return rec, err
	}
	contractID := xdr.ContractId(sacID)
	if rec.NativeSAC, err = strkey.Encode(strkey.VersionByteContract, sacID[:]); err != nil {
		return rec, err
	}
	sacAddr := xdr.ScAddress{Type: xdr.ScAddressTypeScAddressTypeContract, ContractId: &contractID}
	deployed, err := e.ensureNativeSAC(sacAddr, from)
	if deployed != nil {
		rec.Submissions = append(rec.Submissions, *deployed)
	}
	if err != nil {
		return rec, err
	}
	accAddr := func(g string) xdr.ScVal {
		aid := xdr.MustAddress(g)
		addr := xdr.ScAddress{Type: xdr.ScAddressTypeScAddressTypeAccount, AccountId: &aid}
		return xdr.ScVal{Type: xdr.ScValTypeScvAddress, Address: &addr}
	}
	amount := xdr.Int128Parts{Hi: 0, Lo: 10_000_000}
	hostFn := xdr.HostFunction{
		Type: xdr.HostFunctionTypeHostFunctionTypeInvokeContract,
		InvokeContract: &xdr.InvokeContractArgs{
			ContractAddress: xdr.ScAddress{Type: xdr.ScAddressTypeScAddressTypeContract, ContractId: &contractID},
			FunctionName:    "transfer",
			Args: xdr.ScVec{
				accAddr(from.Address()),
				accAddr(to.Address()),
				{Type: xdr.ScValTypeScvI128, I128: &amount},
			},
		},
	}

	// wallet-backend reads a Soroban op's participant from op.SourceAccount(), which falls back to
	// the transaction source when the op has none (#716 producer 1). So an M... address can reach
	// it as the operation source or as the transaction source. Since protocol 25 stellar-core
	// rejects both (TransactionFrame::validateSorobanMemo -> txSOROBAN_INVALID); before 25 it was
	// only a transaction-queue filter. We still try both, to record the rejection.
	// Then the route that is valid today (CAP-67, protocol >= 23): a plain G source, and the SAC
	// transfer's `to` is a muxed ScAddress. wallet-backend #716 lists it as producer 2
	// (SEP-41 muxed ScAddress rendered as M... into AddressBytea).
	toKey := xdr.MustAddress(to.Address()).MustEd25519()
	muxedTo := xdr.ScAddress{
		Type:         xdr.ScAddressTypeScAddressTypeMuxedAccount,
		MuxedAccount: &xdr.MuxedEd25519Account{Id: xdr.Uint64(muxedID), Ed25519: toKey},
	}
	toMuxedFn := hostFn
	toMuxedArgs := *hostFn.InvokeContract
	toMuxedArgs.Args = xdr.ScVec{hostFn.InvokeContract.Args[0], {Type: xdr.ScValTypeScvAddress, Address: &muxedTo}, hostFn.InvokeContract.Args[2]}
	toMuxedFn.InvokeContract = &toMuxedArgs

	attempts := []struct {
		name, txSource, opSource string
		fn                       xdr.HostFunction
	}{
		{"invoke_host_function with muxed op source", from.Address(), muxed.Address(), hostFn},
		{"invoke_host_function with muxed tx source", muxed.Address(), "", hostFn},
		{"sac_transfer_to_muxed_scaddress", from.Address(), "", toMuxedFn},
	}
	var reasons []string
	for _, at := range attempts {
		s, err := e.tryInvoke(at.name, from, at.txSource, at.opSource, at.fn)
		rec.Submissions = append(rec.Submissions, s)
		if err != nil {
			return rec, err
		}
		if s.Status == protocol.TransactionStatusSuccess {
			rec.TargetTx, rec.TargetLedger, rec.TargetPattern = s.Hash, s.Ledger, at.name
			return rec, nil
		}
		reasons = append(reasons, fmt.Sprintf("%s: %s %s%s", at.name, s.Status, s.ErrorResult, s.Error))
	}
	rec.FallbackReason = strings.Join(reasons, "; ")

	// Fallback: classic payment, muxed source and muxed destination.
	logf("falling back to a classic payment: %s", rec.FallbackReason)
	toMuxed, err := xdr.MuxedAccountFromAccountId(to.Address(), muxedID)
	if err != nil {
		return rec, err
	}
	src, err = e.loadAccount(from.Address())
	if err != nil {
		return rec, err
	}
	tx, err = newTx(src, txnbuild.MinBaseFee, &txnbuild.Payment{
		SourceAccount: muxed.Address(), Destination: toMuxed.Address(), Amount: "1", Asset: txnbuild.NativeAsset{},
	})
	if err != nil {
		return rec, err
	}
	s, err = mustSucceed(e.submit("fallback: payment M -> M", tx, from))
	rec.Submissions = append(rec.Submissions, s)
	if err != nil {
		return rec, err
	}
	rec.TargetTx, rec.TargetLedger, rec.TargetPattern = s.Hash, s.Ledger, "payment_muxed_source_and_destination"
	return rec, nil
}

// ---------------------------------------------------------------------------------------------
// lp-issuer-order-01: change_trust on a liquidity-pool share whose two assets have the same code
// and issuers that sort one way by raw key bytes (what the network uses) and the other way by
// G... strkey text (base32: 2-7 encode values after A-Z, but sort before them in ASCII).

type LPRecord struct {
	Entry        string       `json:"entry"`
	Network      string       `json:"network_passphrase"`
	Code         string       `json:"asset_code"`
	IssuerA      string       `json:"issuer_a"` // first by key bytes (network order)
	IssuerB      string       `json:"issuer_b"`
	Trustor      string       `json:"trustor"`
	PoolID       string       `json:"pool_id"`
	GrindTries   int          `json:"grind_tries"`
	Submissions  []Submission `json:"submissions"`
	TargetTx     string       `json:"target_tx_hash"`
	TargetLedger uint32       `json:"target_ledger"`
}

const lpCode = "LPO"

func (e *env) lpIssuerOrder() (*LPRecord, error) {
	// Grind two issuers whose byte order and strkey text order disagree, with the difference
	// in the position where one strkey has a letter and the other a digit.
	var a, b *keypair.Full
	tries := 0
	for {
		tries++
		x, y := keypair.MustRandom(), keypair.MustRandom()
		xk, yk := xdr.MustAddress(x.Address()).MustEd25519(), xdr.MustAddress(y.Address()).MustEd25519()
		bytesLess := bytes.Compare(xk[:], yk[:]) < 0
		textLess := x.Address() < y.Address()
		if bytesLess != textLess {
			if bytesLess {
				a, b = x, y
			} else {
				a, b = y, x
			}
			break
		}
	}
	rec := &LPRecord{Entry: "lp-issuer-order-01", Network: e.passphrase, Code: lpCode, IssuerA: a.Address(), IssuerB: b.Address(), GrindTries: tries}
	logf("issuers: by bytes %s < %s; by text the other way (%d tries)", a.Address(), b.Address(), tries)

	funder, err := e.fundWithFriendbot()
	if err != nil {
		return rec, err
	}
	trustor := keypair.MustRandom()
	rec.Trustor = trustor.Address()
	assetA := txnbuild.CreditAsset{Code: lpCode, Issuer: a.Address()}
	assetB := txnbuild.CreditAsset{Code: lpCode, Issuer: b.Address()}
	pool := txnbuild.LiquidityPoolParameters{AssetA: assetA, AssetB: assetB, Fee: txnbuild.LiquidityPoolFeeV18}
	poolID, err := txnbuild.NewLiquidityPoolId(assetA, assetB)
	if err != nil {
		return rec, fmt.Errorf("network order rejected by the Go SDK: %w", err)
	}
	rec.PoolID = fmt.Sprintf("%x", poolID[:])
	// One transaction: create issuers and trustor, then the trustor trusts both assets and the pool share.
	src, err := e.loadAccount(funder.Address())
	if err != nil {
		return rec, err
	}
	buildTarget := func(src *txnbuild.SimpleAccount) (*txnbuild.Transaction, error) {
		return newTx(src, txnbuild.MinBaseFee,
			&txnbuild.CreateAccount{Destination: a.Address(), Amount: "2"},
			&txnbuild.CreateAccount{Destination: b.Address(), Amount: "2"},
			&txnbuild.CreateAccount{Destination: trustor.Address(), Amount: "10"},
			&txnbuild.ChangeTrust{SourceAccount: trustor.Address(), Line: assetA.MustToChangeTrustAsset()},
			&txnbuild.ChangeTrust{SourceAccount: trustor.Address(), Line: assetB.MustToChangeTrustAsset()},
			&txnbuild.ChangeTrust{SourceAccount: trustor.Address(), Line: txnbuild.LiquidityPoolShareChangeTrustAsset{LiquidityPoolParameters: pool}},
		)
	}
	seq := src.Sequence

	// Negative probe first: the same transaction with the pool assets in strkey-text order, built
	// by swapping them in the XDR (the Go SDK refuses to build it). Core must reject it.
	probe, err := buildTarget(&txnbuild.SimpleAccount{AccountID: funder.Address(), Sequence: seq})
	if err != nil {
		return rec, err
	}
	envXDR := probe.ToXDR()
	lpOp := envXDR.V1.Tx.Operations[5].Body.ChangeTrustOp
	params := lpOp.Line.LiquidityPool.ConstantProduct
	params.AssetA, params.AssetB = params.AssetB, params.AssetA
	hash, err := network.HashTransactionInEnvelope(envXDR, e.passphrase)
	if err != nil {
		return rec, err
	}
	envXDR.V1.Signatures = nil
	for _, kp := range []*keypair.Full{funder, trustor} {
		sig, err := kp.SignDecorated(hash[:])
		if err != nil {
			return rec, err
		}
		envXDR.V1.Signatures = append(envXDR.V1.Signatures, sig)
	}
	probeB64, err := xdr.MarshalBase64(envXDR)
	if err != nil {
		return rec, err
	}
	s, err := e.submitEnvelope("probe: pool assets in strkey-text order (must be rejected)", probeB64, hash)
	rec.Submissions = append(rec.Submissions, s)
	if err != nil {
		return rec, err
	}
	if s.Status == protocol.TransactionStatusSuccess {
		return rec, fmt.Errorf("core accepted the text-ordered pool; the pattern does not discriminate")
	}

	// Target: network (byte) order. If the probe reached a ledger it consumed the sequence number.
	if s.Accepted {
		seq++
	}
	tx, err := buildTarget(&txnbuild.SimpleAccount{AccountID: funder.Address(), Sequence: seq})
	if err != nil {
		return rec, err
	}
	s, err = mustSucceed(e.submit("target: change_trust on the pool share in network order", tx, funder, trustor))
	rec.Submissions = append(rec.Submissions, s)
	if err != nil {
		return rec, err
	}
	rec.TargetTx, rec.TargetLedger = s.Hash, s.Ledger
	return rec, nil
}

// tryInvoke simulates, assembles and submits one invoke_host_function transaction. A failed
// simulation or a rejection by core is recorded in the Submission, not returned as an error.
func (e *env) tryInvoke(name string, signer *keypair.Full, txSource, opSource string, fn xdr.HostFunction) (Submission, error) {
	purpose := "target: " + name
	acc, err := e.loadAccount(signer.Address())
	if err != nil {
		return Submission{Purpose: purpose}, err
	}
	seq := acc.Sequence
	invoke := &txnbuild.InvokeHostFunction{SourceAccount: opSource, HostFunction: fn}
	tx, err := newTx(&txnbuild.SimpleAccount{AccountID: txSource, Sequence: seq}, txnbuild.MinBaseFee, invoke)
	if err != nil {
		return Submission{Purpose: purpose}, fmt.Errorf("%s: building: %w", name, err)
	}
	b64, err := tx.Base64()
	if err != nil {
		return Submission{Purpose: purpose}, err
	}
	sim, err := e.rpc.SimulateTransaction(e.ctx, protocol.SimulateTransactionRequest{Transaction: b64})
	if err == nil && sim.Error != "" {
		err = errors.New(sim.Error)
	}
	if err != nil {
		logf("%s: simulation failed: %v", name, err)
		return Submission{Purpose: purpose, Status: "SIMULATION_FAILED", Error: err.Error()}, nil
	}
	var sorobanData xdr.SorobanTransactionData
	if err := xdr.SafeUnmarshalBase64(sim.TransactionDataXDR, &sorobanData); err != nil {
		return Submission{Purpose: purpose}, fmt.Errorf("decoding transactionData: %w", err)
	}
	invoke.Ext = xdr.TransactionExt{V: 1, SorobanData: &sorobanData}
	if len(sim.Results) == 1 && sim.Results[0].AuthXDR != nil {
		for _, a := range *sim.Results[0].AuthXDR {
			var entry xdr.SorobanAuthorizationEntry
			if err := xdr.SafeUnmarshalBase64(a, &entry); err != nil {
				return Submission{Purpose: purpose}, fmt.Errorf("decoding auth: %w", err)
			}
			invoke.Auth = append(invoke.Auth, entry)
		}
	}
	// 15% headroom on the resource fee; base fee 100 stroops.
	fee := txnbuild.MinBaseFee + sim.MinResourceFee + sim.MinResourceFee*15/100
	tx, err = newTx(&txnbuild.SimpleAccount{AccountID: txSource, Sequence: seq}, fee, invoke)
	if err != nil {
		return Submission{Purpose: purpose}, err
	}
	return e.submit(purpose, tx, signer)
}

// ensureNativeSAC deploys the native asset's Stellar Asset Contract if the network has none yet
// (testnet has it; a fresh local quickstart does not). Returns the deploy submission, if any.
func (e *env) ensureNativeSAC(sac xdr.ScAddress, signer *keypair.Full) (*Submission, error) {
	key := xdr.LedgerKey{
		Type: xdr.LedgerEntryTypeContractData,
		ContractData: &xdr.LedgerKeyContractData{
			Contract:   sac,
			Key:        xdr.ScVal{Type: xdr.ScValTypeScvLedgerKeyContractInstance},
			Durability: xdr.ContractDataDurabilityPersistent,
		},
	}
	b64, err := xdr.MarshalBase64(key)
	if err != nil {
		return nil, err
	}
	got, err := e.rpc.GetLedgerEntries(e.ctx, protocol.GetLedgerEntriesRequest{Keys: []string{b64}})
	if err != nil {
		return nil, fmt.Errorf("looking up the native SAC: %w", err)
	}
	if len(got.Entries) > 0 {
		return nil, nil
	}
	logf("native SAC not deployed on this network; deploying it")
	native := xdr.MustNewNativeAsset()
	fn := xdr.HostFunction{
		Type: xdr.HostFunctionTypeHostFunctionTypeCreateContract,
		CreateContract: &xdr.CreateContractArgs{
			ContractIdPreimage: xdr.ContractIdPreimage{Type: xdr.ContractIdPreimageTypeContractIdPreimageFromAsset, FromAsset: &native},
			Executable:         xdr.ContractExecutable{Type: xdr.ContractExecutableTypeContractExecutableStellarAsset},
		},
	}
	s, err := e.tryInvoke("deploy native SAC", signer, signer.Address(), "", fn)
	s.Purpose = "setup: deploy native SAC"
	if err == nil && s.Status != protocol.TransactionStatusSuccess {
		err = fmt.Errorf("deploying the native SAC: %s %s%s", s.Status, s.ErrorResult, s.Error)
	}
	return &s, err
}
