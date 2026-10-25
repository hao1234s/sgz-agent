import test from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

const fixture=fileURLToPath(new URL('./fixture-worker.mjs',import.meta.url));

test('routes out-of-order responses by id and separates stderr',async()=>{
  const {WorkerClient}=await import('../dist/worker.js');
  const client=new WorkerClient(process.execPath,[fixture],process.cwd());
  try {
    const [a,b]=await Promise.all([client.call('stderr',{delay:50,value:'a'}),client.call('echo',{value:'b'})]);
    assert.equal(a.echo.value,'a');assert.equal(b.echo.value,'b');
  }finally{await client.close();}
});

test('child exit rejects pending calls',async()=>{
  const {WorkerClient}=await import('../dist/worker.js');
  const client=new WorkerClient(process.execPath,[fixture],process.cwd());
  await assert.rejects(client.call('exit',{}),/worker_exited/);
  await client.close();
});

test('timeout closes worker instead of retrying a possible mutation',async()=>{
  const {WorkerClient}=await import('../dist/worker.js');
  const client=new WorkerClient(process.execPath,[fixture],process.cwd());
  await assert.rejects(client.call('hang',{},100),/worker_timeout/);
  await assert.rejects(client.call('echo',{}),/worker_closed/);
  await client.close();
});
