//! map-echo: returns its map argument unchanged.
//!
//! Used by `gen scmap-collision` (corpus entry cap0046-scmap-key-collision-01): the call puts an
//! ScMap with keys of different types that print the same (u32 1, string "1", symbol "1") into the
//! transaction envelope, and the network writes it back as the call's return value in the ledger meta.
#![no_std]
use soroban_sdk::{contract, contractimpl, Env, Map, Val};

#[contract]
pub struct MapEcho;

#[contractimpl]
impl MapEcho {
    pub fn echo(_env: Env, m: Map<Val, Val>) -> Map<Val, Val> {
        m
    }
}
