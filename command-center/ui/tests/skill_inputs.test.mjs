import assert from 'node:assert/strict';
import {test} from 'node:test';
import {collectSkillInputs, createSkillField} from '../skill_inputs.js';
const schema = {type:'object', additionalProperties:false, required:['mode'], properties:{
 mode:{type:'string',enum:['search','supplied']}, query:{type:'string'},
 articles:{type:'array',maxItems:40,items:{type:'object',required:['url'],properties:{url:{type:'string'}},additionalProperties:false}},
 limit:{type:'integer',minimum:1,maximum:20}, enabled:{type:'boolean'}},
 allOf:[{if:{properties:{mode:{const:'supplied'}}},then:{required:['articles']}},{if:{properties:{mode:{const:'search'}}},then:{required:['query']}}]};
test('news array and limit stay typed; absent optionals omitted',()=>{
 const got=collectSkillInputs(schema,{mode:'supplied',articles:'[{"url":"https://example.com"}]',limit:'5',query:''});
 assert.equal(got.limit,5);assert.equal(got.articles[0].url,'https://example.com');assert.equal('query' in got,false);
});
test('false and zero are actual values, not missing',()=>{
 const got=collectSkillInputs({required:['x','y'],properties:{x:{type:'boolean'},y:{type:'integer'}}},{x:'false',y:'0'});
 assert.equal(got.x,false);assert.equal(got.y,0);
});
test('multiline mimik guide survives verbatim and unused snapshot omitted',()=>{
 const markdown='# Guide\n\n---\n## Step 01: Click Models\n';
 const got=collectSkillInputs({properties:{markdown:{type:'string'},snapshot:{type:'object'}},oneOf:[{required:['markdown']},{required:['snapshot']}]},{markdown,snapshot:''});
 assert.equal(got.markdown,markdown);assert.equal('snapshot' in got,false);
});
for (const [label,raw] of Object.entries({badjson:{articles:'[bad'},wrongtype:{articles:'{}'},missing:{},invalidenum:{mode:'bogus'},boolnumber:{limit:'true'},infinite:{limit:'1e999'},negative:{limit:'-1'},fraction:{limit:'1.5'},extra:{articles:'[{"url":"x","token":"no"}]'}})) {
 test(`invalid ${label} is rejected before POST`,()=>assert.throws(()=>collectSkillInputs(schema,{mode:'supplied',articles:'[]',...raw,...(label==='missing'?{articles:''}:{})})));
}
test('conditional search requires query',()=>assert.throws(()=>collectSkillInputs(schema,{mode:'search',query:''})));
test('mimik rejects both alternatives',()=>assert.throws(()=>collectSkillInputs({properties:{markdown:{type:'string'},snapshot:{type:'object'}},oneOf:[{required:['markdown']},{required:['snapshot']}]},{markdown:'x',snapshot:'{}'})));
test('prototype keys are blocked',()=>assert.throws(()=>collectSkillInputs({properties:JSON.parse('{"__proto__":{"type":"string"}}')},JSON.parse('{"__proto__":"x"}'))));
test('widgets use textarea for JSON and guide, select for enum',()=>{
 const widgets=Object.fromEntries(['input','textarea','select'].map(kind=>[kind,(...args)=>({kind,args})]));
 assert.equal(createSkillField('articles',{type:'array'},widgets).kind,'textarea');
 assert.equal(createSkillField('snapshot',{type:'object'},widgets).kind,'textarea');
 assert.equal(createSkillField('markdown',{type:'string'},widgets).kind,'textarea');
 assert.equal(createSkillField('mode',{enum:['search','supplied']},widgets).kind,'select');
});
