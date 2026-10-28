"""Reproduce install-and-immediate-call hook ordering in owned processes only."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

import frida
from sgz_agent.native_session import close_native_session


CHILD = r'''
import ctypes,json,sys
lib=ctypes.CDLL('msvcrt.dll')
outer,inner=lib.labs,lib.toupper
outer.argtypes=inner.argtypes=[ctypes.c_int]
outer.restype=inner.restype=ctypes.c_int
print(json.dumps({'outer':hex(ctypes.cast(outer,ctypes.c_void_p).value),'inner':hex(ctypes.cast(inner,ctypes.c_void_p).value)}),flush=True)
for line in sys.stdin:
 if line.strip()=='run':print(outer(-3),flush=True);assert inner(97)==65
'''


def probe(flush,traps='default',chain=False):
    child=subprocess.Popen([sys._base_executable,'-u','-c',CHILD],stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    session=script=None
    try:
        addresses=json.loads(child.stdout.readline())
        assert addresses['outer']!=addresses['inner']
        session=frida.get_local_device().attach(child.pid)
        helper=(Path(__file__).parents[1]/'src/sgz_agent/native_lifecycle.js').read_text('utf-8')
        program='const OPTIONS='+json.dumps({**addresses,'flush':flush,'traps':traps,'chain':chain})+';\n'+helper+r'''
const life=createNativeLifecycle({attach:(p,h)=>Interceptor.attach(p,h),flush:()=>Interceptor.flush(),
 defer:fn=>setImmediate(fn),publish:v=>send(v)});
let count=0,nestedCount=null,ran=false,calling=false;
const wrapper=OPTIONS.chain?new CModule('extern int inner(int n); int wrapper(int n) { return inner(n); }',
 {inner:ptr(OPTIONS.inner)}):null;
life.attach(ptr(OPTIONS.outer),{onEnter(){
 if(ran)return;ran=true;
 life.attach(ptr(OPTIONS.inner),{onEnter(){count++;},onLeave(){
   if(!calling&&nestedCount!==null)life.finish({kind:'complete',data:{nestedCount,naturalCount:count-nestedCount,result:65}});
 }});
 if(OPTIONS.flush)Interceptor.flush();
 calling=true;
 const result=new NativeFunction(wrapper?wrapper.wrapper:ptr(OPTIONS.inner),'int',['int'],{traps:OPTIONS.traps})(97);
 calling=false;nestedCount=count;
 if(result!==65)throw Error('fixture unexpected result');
}});
rpc.exports={close:()=>life.close(),state:()=>life.state()};
'''
        done=threading.Event();messages=[]
        def receive(message,data):
            messages.append(message)
            if message['type']=='error' or message.get('payload',{}).get('kind')=='complete':done.set()
        script=session.create_script(program);script.on('message',receive);script.load()
        child.stdin.write('run\n');child.stdin.flush()
        assert done.wait(5),'owned fixture timeout'
        assert child.stdout.readline().strip()=='3'
        with tempfile.TemporaryDirectory(prefix='sgz-refill-hooks-') as directory:
            close_native_session(SimpleNamespace(data_dir=Path(directory)),session,script)
        assert session.is_detached
        assert not any(m['type']=='error' for m in messages),messages
        result=next(m['payload']['data'] for m in messages if m.get('payload',{}).get('kind')=='complete')
        assert result['result']==65
        return {'flush':flush,'traps':traps,'indirect_chain':chain,**result,'detached':True,'game_access':False}
    finally:
        child.terminate();child.wait(5)
        if session and not session.is_detached:session.detach()


if __name__=='__main__':
    results=[probe(False),probe(True),probe(True,'all'),probe(True,chain=True),probe(True,'all',True)]
    print(json.dumps(results))
    assert all(r['nestedCount']==0 and r['naturalCount']==1 for r in results),results
