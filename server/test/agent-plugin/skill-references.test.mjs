import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, symlink, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSkillExecutor} from '../../dist/agent/plugin/conversation/skills.js';

test('loading a registered skill includes its referenced local markdown without filesystem tools',async()=>{
 const root=await mkdtemp(join(tmpdir(),'sgz-skill-'));
 try {
  const base=join(root,'skills','sgz-test');await mkdir(join(base,'references'),{recursive:true});
  await writeFile(join(base,'references/model.md'),'# Calculation reference\nOnly validated inputs.');
  const skill={name:'sgz-test',provider:'sgz-filesystem',invocation:{modelInvocable:true},resourceBase:{kind:'directory',path:base},content:'Read [model](references/model.md).'};
  const execute=createSkillExecutor({skills:{snapshot:async()=>({skills:[skill],complete:true}),get:async()=>skill}},{rootDir:root,directories:[join(root,'skills')],legacy:false});
  const loaded=await execute('skill_load',{name:'sgz-test'});
  assert.equal(loaded.references['references/model.md'],'# Calculation reference\nOnly validated inputs.');
  await writeFile(join(root,'private.md'),'PRIVATE');await rm(join(base,'references/model.md'));await symlink(join(root,'private.md'),join(base,'references/model.md'));
  await assert.rejects(execute('skill_load',{name:'sgz-test'}),/skill_reference_outside_root/);
 } finally {await rm(root,{recursive:true,force:true});}
});
