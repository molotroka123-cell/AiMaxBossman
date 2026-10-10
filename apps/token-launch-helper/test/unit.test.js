'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { Keypair, PublicKey } = require('@solana/web3.js');
const L = require('../src/launch.js');
const W = Keypair.generate().publicKey.toBase58();
const ok = { name: 'Test Coin', symbol: 'TEST', wallet: W, devBuySol: 0.1 };

test('one wallet only: arrays or a wallets list are refused', () => {
  assert.throws(() => L.validate({ ...ok, wallet: [W, W] }), L.LaunchRefused);
  assert.throws(() => L.validate({ ...ok, wallets: [W] }), L.LaunchRefused);
  assert.doesNotThrow(() => L.validate(ok));
});

test('dev buy bounds: negative, NaN and above the limit are refused; 0 and the limit pass', () => {
  for (const bad of [-0.1, NaN, Infinity, L.MAX_DEV_BUY_SOL + 0.01]) assert.throws(() => L.validate({ ...ok, devBuySol: bad }), L.LaunchRefused);
  assert.equal(L.validate({ ...ok, devBuySol: 0 }).sol, 0);
  assert.equal(L.validate({ ...ok, devBuySol: L.MAX_DEV_BUY_SOL }).sol, L.MAX_DEV_BUY_SOL);
});

test('name, symbol, wallet and priority fee are validated', () => {
  assert.throws(() => L.validate({ ...ok, name: '' }), L.LaunchRefused);
  assert.throws(() => L.validate({ ...ok, name: 'x'.repeat(33) }), L.LaunchRefused);
  assert.throws(() => L.validate({ ...ok, symbol: 'TOO-LONG-SYM' }), L.LaunchRefused);
  assert.throws(() => L.validate({ ...ok, wallet: 'not-a-key' }), L.LaunchRefused);
  assert.throws(() => L.validate({ ...ok, priorityMicroLamports: 1.5 }), L.LaunchRefused);
  assert.equal(L.validate({ ...ok, priorityMicroLamports: 200000 }).pr, 200000);
});

test('the dev buy is disclosed in the description, and only when there is one', () => {
  const w = new PublicKey(W);
  assert.match(L.withDisclosure('My coin', 0.25, w), new RegExp(`Dev buy: 0.25 SOL from ${W}`));
  assert.equal(L.withDisclosure('My coin', 0, w), 'My coin');
});

test('send is refused without the phrase, without a successful simulation, or with a different key', async () => {
  const owner = Keypair.generate();
  const prepared = { wallet: owner.publicKey, mint: Keypair.generate(), tx: null, connection: null };
  await assert.rejects(L.send(prepared, { ownerKeypair: owner, confirmPhrase: 'yes', simulation: { err: null } }), L.LaunchRefused);
  await assert.rejects(L.send(prepared, { ownerKeypair: owner, confirmPhrase: L.CONFIRM_PHRASE, simulation: { err: { InstructionError: [2, 'x'] } } }), L.LaunchRefused);
  await assert.rejects(L.send(prepared, { ownerKeypair: owner, confirmPhrase: L.CONFIRM_PHRASE, simulation: null }), L.LaunchRefused);
  await assert.rejects(L.send(prepared, { ownerKeypair: Keypair.generate(), confirmPhrase: L.CONFIRM_PHRASE, simulation: { err: null } }), L.LaunchRefused);
});

test('cli refuses a repeated --wallet or --dev-buy (one wallet, one buy)', () => {
  const { args } = require('../src/cli.js');
  assert.throws(() => args(['--wallet', W, '--wallet', W]), L.LaunchRefused);
  assert.throws(() => args(['--dev-buy', '0.1', '--dev-buy', '0.2']), L.LaunchRefused);
  assert.deepEqual(args(['--name', 'A', '--send']), { name: 'A', send: true });
});
