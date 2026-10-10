'use strict';
/*
 Dry run (default, no money, nothing sent):
   node src/cli.js --name "My Coin" --symbol MYC --description "..." --wallet <YOUR_PUBKEY> --dev-buy 0.1 [--priority 100000]
 Real launch (owner only): add --image logo.png --upload  (uploads metadata to pump.fun IPFS)  OR  --uri <metadata uri>
   and --send --keypair <path to YOUR keypair json> --confirm "Я ПОДПИСЫВАЮ ЗАПУСК"
*/
const fs = require('node:fs');
const path = require('node:path');
const { Keypair } = require('@solana/web3.js');
const L = require('./launch.js');

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const k = a.slice(2); const v = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
    if (k in out) throw new L.LaunchRefused(`--${k} given twice (one wallet, one buy)`);
    out[k] = v;
  }
  return out;
}

async function uploadMetadata({ image, name, symbol, description }) {
  const form = new FormData();
  form.append('file', new Blob([fs.readFileSync(image)]), path.basename(image));
  form.append('name', name); form.append('symbol', symbol); form.append('description', description); form.append('showName', 'true');
  const r = await fetch('https://pump.fun/api/ipfs', { method: 'POST', body: form });
  if (!r.ok) throw new Error(`metadata upload failed: HTTP ${r.status}`);
  const j = await r.json();
  if (!j.metadataUri) throw new Error('metadata upload returned no metadataUri');
  return j.metadataUri;
}

async function main() {
  const a = args(process.argv.slice(2));
  const opts = { name: a.name, symbol: a.symbol, description: a.description || '', wallet: a.wallet,
    devBuySol: a['dev-buy'] === undefined ? 0 : Number(a['dev-buy']), priorityMicroLamports: a.priority === undefined ? 0 : Number(a.priority), rpc: a.rpc };
  const { sol, wallet } = L.validate(opts);
  if (a.upload) {
    if (!a.send) throw new L.LaunchRefused('--upload only together with --send (a dry run uploads nothing)');
    opts.uri = await uploadMetadata({ image: a.image, name: opts.name, symbol: opts.symbol, description: L.withDisclosure(opts.description, sol, wallet) });
  } else if (a.uri) opts.uri = String(a.uri);
  const p = await L.prepare(opts);
  const sim = await L.simulate(p);
  console.log(JSON.stringify({ mint: p.mint.publicKey.toBase58(), wallet: wallet.toBase58(), devBuySol: sol, description: p.description,
    uri: p.uri, pumpInstructions: L.pumpInstructionCount(p.ixs), simulation: { err: sim.err, unitsConsumed: sim.unitsConsumed } }, null, 1));
  if (sim.err !== null) { console.error(sim.logs.slice(-15).join('\n')); process.exit(2); }
  if (!a.send) { console.log('DRY RUN OK: nothing was signed or sent.'); return; }
  if (!opts.uri || opts.uri.includes('example.invalid')) throw new L.LaunchRefused('a real launch needs metadata: --image ... --upload or --uri ...');
  const owner = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(a.keypair, 'utf8'))));
  const res = await L.send(p, { ownerKeypair: owner, confirmPhrase: a.confirm, simulation: sim });
  console.log(JSON.stringify(res, null, 1));
}

if (require.main === module) main().catch((e) => { console.error(`${e.name === 'Error' ? 'ERROR' : e.constructor.name}: ${e.message}`); process.exit(1); });
module.exports = { args };
