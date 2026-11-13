package main

import (
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"fmt"

	"github.com/stellar/go-stellar-sdk/keypair"
	"github.com/stellar/go-stellar-sdk/network"
	protocol "github.com/stellar/go-stellar-sdk/protocols/rpc"
	"github.com/stellar/go-stellar-sdk/strkey"
	"github.com/stellar/go-stellar-sdk/txnbuild"
	"github.com/stellar/go-stellar-sdk/xdr"
)

// ---------------------------------------------------------------------------------------------
// cap0027-muxed-classic-payment-01: a classic payment where the transaction source, the operation
// source and the destination are all muxed (M...) accounts. This is the "muxed source" shape the RFP
// lists; on Soroban it is rejected since protocol 25 (see cap0027-muxed-sac-destination-01), so it
// can only be a classic transaction today. The three muxed ids differ, and all are above 2^53, so a
// reader that keeps ids in a float or mixes up the three accounts is visible.

const (
	muxedIDTxSource uint64 = 0x8000000000000001 // above math.MaxInt64
	muxedIDOpSource uint64 = 0x0020000000000001 // 2^53 + 1: the smallest id a float64 cannot hold
	muxedIDDest     uint64 = 0xFFFFFFFFFFFFFFFF // max uint64
)

type MuxedClassicRecord struct {
	Entry        string       `json:"entry"`
	Network      string       `json:"network_passphrase"`
	From         string       `json:"from"`
	To           string       `json:"to"`
	TxSource     string       `json:"tx_source_muxed"`
	OpSource     string       `json:"op_source_muxed"`
	Destination  string       `json:"destination_muxed"`
	Submissions  []Submission `json:"submissions"`
	TargetTx     string       `json:"target_tx_hash"`
	TargetLedger uint32       `json:"target_ledger"`
}

func (e *env) muxedClassic() (*MuxedClassicRecord, error) {
	from, err := e.fundWithFriendbot()
	if err != nil {
		return nil, err
	}
	to := keypair.MustRandom()
	rec := &MuxedClassicRecord{Entry: "cap0027-muxed-classic-payment-01", Network: e.passphrase, From: from.Address(), To: to.Address()}
	mux := func(g string, id uint64) (string, error) {
		m, err := xdr.MuxedAccountFromAccountId(g, id)
		if err != nil {
			return "", err
		}
		return m.Address(), nil
	}
	if rec.TxSource, err = mux(from.Address(), muxedIDTxSource); err != nil {
		return rec, err
	}
	if rec.OpSource, err = mux(from.Address(), muxedIDOpSource); err != nil {
		return rec, err
	}
	if rec.Destination, err = mux(to.Address(), muxedIDDest); err != nil {
		return rec, err
	}

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

	src, err = e.loadAccount(from.Address())
	if err != nil {
		return rec, err
	}
	// The transaction source is set on the built envelope (txnbuild takes a G... account here).
	tx, err = newTx(src, txnbuild.MinBaseFee, &txnbuild.Payment{
		SourceAccount: rec.OpSource, Destination: rec.Destination, Amount: "1", Asset: txnbuild.NativeAsset{},
	})
	if err != nil {
		return rec, err
	}
	envXDR := tx.ToXDR()
	txSrc, err := xdr.AddressToMuxedAccount(rec.TxSource)
	if err != nil {
		return rec, err
	}
	envXDR.V1.Tx.SourceAccount = txSrc
	hash, err := network.HashTransactionInEnvelope(envXDR, e.passphrase)
	if err != nil {
		return rec, err
	}
	sig, err := from.SignDecorated(hash[:])
	if err != nil {
		return rec, err
	}
	envXDR.V1.Signatures = []xdr.DecoratedSignature{sig}
	b64, err := xdr.MarshalBase64(envXDR)
	if err != nil {
		return rec, err
	}
	s, err = mustSucceed(e.submitEnvelope("target: payment, muxed tx source, op source and destination", b64, hash))
	rec.Submissions = append(rec.Submissions, s)
	if err != nil {
		return rec, err
	}
	rec.TargetTx, rec.TargetLedger = s.Hash, s.Ledger
	return rec, nil
}

// ---------------------------------------------------------------------------------------------
// cap0046-scmap-key-collision-01: a contract call whose argument and return value are an ScMap with
// the keys u32 1, string "1" and symbol "1". They are three different, validly ordered ScVal keys;
// code that converts map keys to text (e.g. JavaScript object properties) merges them into one.
// The contract (contracts/map-echo) returns its argument unchanged, so the map is in the envelope
// and, as the call's return value, in the ledger meta.

//go:embed contracts/map-echo/map_echo.wasm
var mapEchoWasm []byte

type ScMapRecord struct {
	Entry        string       `json:"entry"`
	Network      string       `json:"network_passphrase"`
	From         string       `json:"from"`
	WasmSHA256   string       `json:"wasm_sha256"`
	Contract     string       `json:"contract"`
	Submissions  []Submission `json:"submissions"`
	TargetTx     string       `json:"target_tx_hash"`
	TargetLedger uint32       `json:"target_ledger"`
}

func (e *env) scmapCollision() (*ScMapRecord, error) {
	from, err := e.fundWithFriendbot()
	if err != nil {
		return nil, err
	}
	wasmHash := sha256.Sum256(mapEchoWasm)
	rec := &ScMapRecord{Entry: "cap0046-scmap-key-collision-01", Network: e.passphrase, From: from.Address(), WasmSHA256: fmt.Sprintf("%x", wasmHash)}
	step := func(purpose string, fn xdr.HostFunction) error {
		s, err := e.tryInvoke(purpose, from, from.Address(), "", fn)
		s.Purpose = "setup: " + purpose
		rec.Submissions = append(rec.Submissions, s)
		if err == nil && s.Status != protocol.TransactionStatusSuccess {
			err = fmt.Errorf("%s: %s %s%s", purpose, s.Status, s.ErrorResult, s.Error)
		}
		return err
	}

	if err := step("upload map-echo wasm", xdr.HostFunction{
		Type: xdr.HostFunctionTypeHostFunctionTypeUploadContractWasm,
		Wasm: &mapEchoWasm,
	}); err != nil {
		return rec, err
	}

	var salt xdr.Uint256
	if _, err := rand.Read(salt[:]); err != nil {
		return rec, err
	}
	deployer := xdr.MustAddress(from.Address())
	preimage := xdr.ContractIdPreimage{
		Type:        xdr.ContractIdPreimageTypeContractIdPreimageFromAddress,
		FromAddress: &xdr.ContractIdPreimageFromAddress{Address: xdr.ScAddress{Type: xdr.ScAddressTypeScAddressTypeAccount, AccountId: &deployer}, Salt: salt},
	}
	wh := xdr.Hash(wasmHash)
	if err := step("deploy map-echo", xdr.HostFunction{
		Type: xdr.HostFunctionTypeHostFunctionTypeCreateContractV2,
		CreateContractV2: &xdr.CreateContractArgsV2{
			ContractIdPreimage: preimage,
			Executable:         xdr.ContractExecutable{Type: xdr.ContractExecutableTypeContractExecutableWasm, WasmHash: &wh},
			ConstructorArgs:    xdr.ScVec{},
		},
	}); err != nil {
		return rec, err
	}
	idPre := xdr.HashIdPreimage{
		Type:       xdr.EnvelopeTypeEnvelopeTypeContractId,
		ContractId: &xdr.HashIdPreimageContractId{NetworkId: xdr.Hash(network.ID(e.passphrase)), ContractIdPreimage: preimage},
	}
	raw, err := idPre.MarshalBinary()
	if err != nil {
		return rec, err
	}
	cid := xdr.ContractId(sha256.Sum256(raw))
	if rec.Contract, err = strkey.Encode(strkey.VersionByteContract, cid[:]); err != nil {
		return rec, err
	}

	// Keys in ScVal order (by type: u32 < string < symbol), as the host requires for a valid ScMap.
	one := xdr.Uint32(1)
	str := xdr.ScString("1")
	sym := xdr.ScSymbol("1")
	val := func(n uint32) xdr.ScVal { v := xdr.Uint32(n); return xdr.ScVal{Type: xdr.ScValTypeScvU32, U32: &v} }
	m := &xdr.ScMap{
		{Key: xdr.ScVal{Type: xdr.ScValTypeScvU32, U32: &one}, Val: val(10)},
		{Key: xdr.ScVal{Type: xdr.ScValTypeScvString, Str: &str}, Val: val(20)},
		{Key: xdr.ScVal{Type: xdr.ScValTypeScvSymbol, Sym: &sym}, Val: val(30)},
	}
	mp := &m
	s, err := mustSucceed(e.tryInvoke("echo(map with keys u32 1, string \"1\", symbol \"1\")", from, from.Address(), "", xdr.HostFunction{
		Type: xdr.HostFunctionTypeHostFunctionTypeInvokeContract,
		InvokeContract: &xdr.InvokeContractArgs{
			ContractAddress: xdr.ScAddress{Type: xdr.ScAddressTypeScAddressTypeContract, ContractId: &cid},
			FunctionName:    "echo",
			Args:            xdr.ScVec{{Type: xdr.ScValTypeScvMap, Map: mp}},
		},
	}))
	rec.Submissions = append(rec.Submissions, s)
	if err != nil {
		return rec, err
	}
	rec.TargetTx, rec.TargetLedger = s.Hash, s.Ledger
	return rec, nil
}
