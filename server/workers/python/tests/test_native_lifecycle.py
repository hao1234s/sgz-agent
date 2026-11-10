"""Exercise the real JS lifecycle with deterministic native callback boundaries."""
import json
from pathlib import Path
import subprocess


def test_result_is_not_delivered_until_all_native_calls_return_and_hooks_detach():
    source = Path(__file__).parents[1] / 'src/sgz_agent/client/native_lifecycle.js'
    program = r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const queue=[],calls=[],hooks=[];
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const life=context.createNativeLifecycle({
 attach(address,callbacks){const h={callbacks,detach(){calls.push('detach:'+address);}};hooks.push(h);return h;},
 flush(){calls.push('flush');},defer(fn){queue.push(fn);},
 publish(message){calls.push(message.kind);}
});
function pump(){let n=0;while(queue.length){assert.ok(++n<20);queue.shift()();}}
life.attach('lua',{onEnter(){life.finish({kind:'complete',data:{ok:true}});}});
const invocation={};hooks[0].callbacks.onEnter.call(invocation,[]);
pump();assert.deepEqual(calls,[],'no send or detach during native execution');
hooks[0].callbacks.onLeave.call(invocation,0);
assert.deepEqual(calls,[],'do not publish from onLeave itself');
pump();assert.deepEqual(calls,['detach:lua','flush','complete']);
assert.equal(life.state().active_native,0);assert.equal(life.state().closed,true);
// A late/unpaired leave must not make the drained count negative. Neither may
// a duplicate delivery consume another invocation's native lifetime.
hooks[0].callbacks.onLeave.call({},0);
hooks[0].callbacks.onLeave.call(invocation,0);
assert.equal(life.state().active_native,0);
// Native invocations queued before detach may enter JS after closing. They must
// not start new Lua work or create a lifetime that detach may never deliver back.
const late={};hooks[0].callbacks.onEnter.call(late,[]);
assert.equal(life.state().active_native,0);
hooks[0].callbacks.onLeave.call(late,0);
assert.equal(life.state().active_native,0);

const other=[];
const drain=context.createNativeLifecycle({
 attach(address,callbacks){other.push(callbacks);return {detach(){calls.push('detached-other');}};},
 flush(){},defer(fn){queue.push(fn);},publish(){assert.fail('cancel published a result');}
});
drain.attach('decoder',{onLeave(){calls.push('decoder-read');}});
const a={},b={};other[0].onEnter.call(a,[]);other[0].onEnter.call(b,[]);
let closed=false;drain.close().then(()=>closed=true);
pump();assert.equal(drain.state().closed,false);
other[0].onLeave.call(a,0);pump();assert.equal(drain.state().closed,false);
other[0].onLeave.call(b,0);pump();
assert.equal(drain.state().closed,true);assert.ok(!calls.includes('decoder-read'));
Promise.resolve().then(()=>{assert.equal(closed,true);process.stdout.write(JSON.stringify({ok:true}));});
'''
    result = subprocess.run(['node','-e',program,str(source)], capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok'] is True
