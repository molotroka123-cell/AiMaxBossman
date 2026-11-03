// usage: node export_glb.mjs gen.js out.glb   — gen.js defines createModel(THREE) -> THREE.Object3D (no DOM)
import * as THREE from 'three';
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js';
import fs from 'fs';
globalThis.FileReader = class { readAsArrayBuffer(b){ b.arrayBuffer().then(r=>{this.result=r; this.onloadend&&this.onloadend();}); }
  readAsDataURL(b){ b.arrayBuffer().then(r=>{this.result='data:application/octet-stream;base64,'+Buffer.from(r).toString('base64'); this.onloadend&&this.onloadend();}); } };
const [,, src, out] = process.argv;
const code = fs.readFileSync(src, 'utf8');
const fn = new Function('THREE', code + '\n;return (typeof createModel!=="undefined")?createModel:null;');
const create = fn(THREE);
if (!create) { console.error('NO createModel'); process.exit(2); }
const obj = create(THREE);
obj.updateMatrixWorld(true);
let tris = 0, meshes = 0;
obj.traverse(o => { if (o.isMesh) { meshes++; const g=o.geometry; tris += g.index ? g.index.count/3 : g.attributes.position.count/3; } });
if (!meshes) { console.error('NO MESHES'); process.exit(3); }
new GLTFExporter().parse(obj, res => { fs.writeFileSync(out, Buffer.from(res)); console.log(JSON.stringify({meshes, tris, bytes: fs.statSync(out).size})); }, e => { console.error('EXPORT FAIL', e); process.exit(4); }, { binary: true });
