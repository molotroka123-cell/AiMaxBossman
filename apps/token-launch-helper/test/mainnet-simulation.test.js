'use strict';
/** Integration: builds the REAL transaction against mainnet state and simulates it (no signature, nothing is sent). */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { Connection, PublicKey, Keypair, LAMPORTS_PER_SOL } = require('@solana/web3.js');
const { PUMP_SDK, PUMP_PROGRAM_ID } = require('@pump-fun/pump-sdk');
const L = require('../src/launch.js');

const RPC = process.env.SOLANA_RPC || 'https://api.mainnet-beta.solana.com';
// Simulation only: a public, well-funded mainnet address is used as fee payer; nothing is signed or sent.
const PAYER = process.env.LAUNCH_SIM_PAYER || '5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9';

test('create_v2 + ONE dev buy in the same transaction simulates successfully on mainnet', { timeout: 120000 }, async (t) => {
  const connection = new Connection(RPC, 'confirmed');
  const bal = await connection.getBalance(new PublicKey(PAYER));
  if (bal < 0.5 * LAMPORTS_PER_SOL) return t.skip(`simulation payer has only ${bal / LAMPORTS_PER_SOL} SOL`);
  const mint = Keypair.generate();
  const p = await L.prepare({ name: 'Sim Coin', symbol: 'SIMC', description: 'simulation only', wallet: PAYER, devBuySol: 0.05, priorityMicroLamports: 100000 }, { connection, mint });
  assert.equal(L.pumpInstructionCount(p.ixs), 2, 'exactly create_v2 + one buy');
  const pumpIxs = p.ixs.filter((ix) => ix.programId.equals(PUMP_PROGRAM_ID));
  const createAlone = await PUMP_SDK.createV2Instruction({ mint: mint.publicKey, name: 'Sim Coin', symbol: 'SIMC', uri: p.uri, creator: p.wallet, user: p.wallet, mayhemMode: false });
  assert.deepEqual(Buffer.from(pumpIxs[0].data), Buffer.from(createAlone.data), 'the first pump instruction is the create');
  assert.ok(p.ixs.indexOf(pumpIxs[1]) > p.ixs.indexOf(pumpIxs[0]), 'the buy comes after the create, in the same transaction');
  assert.match(p.description, /Dev buy: 0.05 SOL from /);
  const sim = await L.simulate(p);
  fs.mkdirSync(path.join(__dirname, '..', 'evidence'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, '..', 'evidence', 'mainnet-simulation.json'),
    JSON.stringify({ at: new Date().toISOString(), rpc: RPC, payer: PAYER, mint: mint.publicKey.toBase58(), err: sim.err, unitsConsumed: sim.unitsConsumed, logs: sim.logs }, null, 1));
  assert.equal(sim.err, null, JSON.stringify(sim.err) + '\n' + sim.logs.slice(-12).join('\n'));
  assert.ok(sim.logs.some((l) => /Instruction: (Create|CreateV2)/i.test(l)), 'program log shows the create');
  assert.ok(sim.logs.some((l) => /Instruction: Buy/i.test(l)), 'program log shows the buy');
});

test('without a dev buy the transaction is a single create_v2', { timeout: 120000 }, async (t) => {
  const connection = new Connection(RPC, 'confirmed');
  const p = await L.prepare({ name: 'Sim Coin', symbol: 'SIMC', wallet: PAYER, devBuySol: 0 }, { connection });
  assert.equal(L.pumpInstructionCount(p.ixs), 1);
});
