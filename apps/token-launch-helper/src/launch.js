'use strict';
/**
 * One pump.fun token + ONE transparent dev buy from ONE wallet, in the SAME transaction (create_v2 then buy):
 * the dev buy is atomically the first buy on the curve, so no sniper can get in between.
 * Built locally with the official @pump-fun/pump-sdk; simulated on mainnet before any send; signed only locally by the owner.
 * No multi-wallet buys, no automated trading, no selling, no loops (see TZ.md).
 */
const { Connection, PublicKey, Keypair, TransactionMessage, VersionedTransaction, ComputeBudgetProgram, LAMPORTS_PER_SOL } = require('@solana/web3.js');
const BN = require('bn.js');
const { OnlinePumpSdk, PUMP_SDK, PUMP_PROGRAM_ID, getBuyTokenAmountFromSolAmount } = require('@pump-fun/pump-sdk');

const MAX_DEV_BUY_SOL = 5;            // hard ceiling of this tool
const CU_LIMIT = 500_000;
const CONFIRM_PHRASE = 'Я ПОДПИСЫВАЮ ЗАПУСК';

class LaunchRefused extends Error {}

function validate(o) {
  if (!o || typeof o !== 'object') throw new LaunchRefused('no options');
  if (Array.isArray(o.wallet) || Array.isArray(o.wallets) || o.wallets !== undefined) throw new LaunchRefused('exactly one wallet: multi-wallet buys are not supported');
  if (typeof o.name !== 'string' || !o.name.trim() || o.name.length > 32) throw new LaunchRefused('name must be 1..32 chars');
  if (typeof o.symbol !== 'string' || !/^[A-Za-z0-9$]{1,10}$/.test(o.symbol)) throw new LaunchRefused('symbol must be 1..10 latin letters/digits');
  const sol = Number(o.devBuySol ?? 0);
  if (!Number.isFinite(sol) || sol < 0) throw new LaunchRefused('dev buy must be a finite number >= 0');
  if (sol > MAX_DEV_BUY_SOL) throw new LaunchRefused(`dev buy above the tool limit of ${MAX_DEV_BUY_SOL} SOL`);
  const pr = Number(o.priorityMicroLamports ?? 0);
  if (!Number.isInteger(pr) || pr < 0 || pr > 5_000_000) throw new LaunchRefused('priority fee must be an integer 0..5,000,000 micro-lamports per CU');
  let wallet;
  try { wallet = new PublicKey(String(o.wallet)); } catch { throw new LaunchRefused('wallet must be one base58 public key'); }
  return { sol, pr, wallet };
}

function withDisclosure(description, sol, wallet) {
  const base = String(description || '').trim();
  if (!(sol > 0)) return base;
  return `${base}${base ? '\n' : ''}Dev buy: ${sol} SOL from ${wallet.toBase58()} (in the creation transaction).`;
}

async function buildLaunchInstructions({ sdk, wallet, mint, name, symbol, uri, sol, pr }) {
  const global = await sdk.fetchGlobal();
  const ixs = [ComputeBudgetProgram.setComputeUnitLimit({ units: CU_LIMIT })];
  if (pr > 0) ixs.push(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: pr }));
  if (sol > 0) {
    const feeConfig = await sdk.fetchFeeConfig();
    const solAmount = new BN(Math.round(sol * LAMPORTS_PER_SOL));
    const amount = getBuyTokenAmountFromSolAmount({ global, feeConfig, mintSupply: null, bondingCurve: null, amount: solAmount });
    ixs.push(...await PUMP_SDK.createV2AndBuyInstructions({ global, mint, name, symbol, uri, creator: wallet, user: wallet,
      amount, solAmount, mayhemMode: false }));
  } else {
    ixs.push(await PUMP_SDK.createV2Instruction({ mint, name, symbol, uri, creator: wallet, user: wallet, mayhemMode: false }));
  }
  return ixs;
}

function pumpInstructionCount(ixs) {
  return ixs.filter((ix) => ix.programId.equals(PUMP_PROGRAM_ID)).length;
}

async function prepare(opts, { connection, mint } = {}) {
  const { sol, pr, wallet } = validate(opts);
  connection = connection || new Connection(opts.rpc || 'https://api.mainnet-beta.solana.com', 'confirmed');
  mint = mint || Keypair.generate();
  const sdk = new OnlinePumpSdk(connection);
  const description = withDisclosure(opts.description, sol, wallet);
  const uri = opts.uri || 'https://example.invalid/metadata-not-uploaded.json';   // dry run: metadata is not uploaded
  const ixs = await buildLaunchInstructions({ sdk, wallet, mint: mint.publicKey, name: opts.name, symbol: opts.symbol, uri, sol, pr });
  const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash('confirmed');
  const msg = new TransactionMessage({ payerKey: wallet, recentBlockhash: blockhash, instructions: ixs }).compileToV0Message();
  return { connection, mint, wallet, sol, pr, description, uri, ixs, tx: new VersionedTransaction(msg), lastValidBlockHeight };
}

async function simulate(prepared) {
  const r = await prepared.connection.simulateTransaction(prepared.tx, { sigVerify: false, replaceRecentBlockhash: true, commitment: 'confirmed' });
  return { err: r.value.err, unitsConsumed: r.value.unitsConsumed, logs: r.value.logs || [] };
}

async function send(prepared, { ownerKeypair, confirmPhrase, simulation }) {
  if (confirmPhrase !== CONFIRM_PHRASE) throw new LaunchRefused(`send needs the exact confirmation phrase: ${CONFIRM_PHRASE}`);
  if (!simulation || simulation.err !== null) throw new LaunchRefused('send refused: no successful simulation of this exact transaction');
  if (!ownerKeypair || !ownerKeypair.publicKey.equals(prepared.wallet)) throw new LaunchRefused('the signing key must be the declared wallet');
  prepared.tx.sign([ownerKeypair, prepared.mint]);
  const sig = await prepared.connection.sendRawTransaction(prepared.tx.serialize(), { skipPreflight: false, maxRetries: 3 });
  await prepared.connection.confirmTransaction({ signature: sig, blockhash: prepared.tx.message.recentBlockhash, lastValidBlockHeight: prepared.lastValidBlockHeight }, 'confirmed');
  return { signature: sig, explorer: `https://solscan.io/tx/${sig}`, mint: prepared.mint.publicKey.toBase58() };
}

module.exports = { validate, withDisclosure, buildLaunchInstructions, pumpInstructionCount, prepare, simulate, send, LaunchRefused, MAX_DEV_BUY_SOL, CONFIRM_PHRASE, CU_LIMIT };
