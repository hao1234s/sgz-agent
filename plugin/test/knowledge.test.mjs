import test from 'node:test';
import assert from 'node:assert/strict';
import {apply} from '../dist/index.js';

test('knowledge tools are read-only and accept explicit category and season filters',()=>{
  const registered=[];
  apply({tools:{register:t=>registered.push(t)},systemPrompt:{section:()=>{}},effect:()=>{}},{rootDir:process.cwd()});
  const search=registered.find(t=>t.name==='sgz_knowledge_search');
  assert.ok(search);
  assert.deepEqual(search.parameters.required,['query']);
  assert.deepEqual(search.parameters.properties.kind.enum,['general','tactic','mechanic']);
  assert.ok(search.parameters.properties.season);
  assert.equal(search.isConcurrencySafe(),true);
  assert.ok(registered.find(t=>t.name==='sgz_knowledge_status'));
});
