"""Probe the real Frida runtime against an owned disposable process, never the game."""
import json
from pathlib import Path
import subprocess
import sys
import threading

import frida


child_code = '''
import ctypes,json,os,sys,threading
print(json.dumps({'pid':os.getpid()}),flush=True)
for line in sys.stdin:
    command=json.loads(line)
    if command.get('quit'):break
    call=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int)(int(command['address'],16))
    threads=[threading.Thread(target=lambda:[call(5) for _ in range(200)]) for _ in range(4)]
    for thread in threads:thread.start()
    for thread in threads:thread.join()
    print(json.dumps({'value':call(5)}),flush=True)
'''
child = subprocess.Popen([sys._base_executable,'-u','-c',child_code],stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
session=script=None
try:
    pid=json.loads(child.stdout.readline())['pid']
    assert pid==child.pid
    session=frida.get_local_device().attach(pid)
    helper=(Path(__file__).parents[1]/'src/sgz_agent/native_lifecycle.js').read_text(encoding='utf-8')
    if '--baseline-before-close-gate' in sys.argv:
        helper=helper.replace("          if (closing) return; // A queued entry after detach starts no Lua work.\n",'')
    program=helper+r'''
const fixture=new CModule('int fixture_add(int value){return value+7;}');
const life=createNativeLifecycle({attach:(p,h)=>Interceptor.attach(p,h),
 flush:()=>Interceptor.flush(),defer:fn=>setImmediate(fn),publish:value=>send(value)});
const recursive=new NativeFunction(fixture.fixture_add,'int',['int']);let busy=false;
life.attach(fixture.fixture_add,{onEnter(){if(busy)return;busy=true;
 try{recursive(2);life.finish({kind:'complete',data:{ok:true}});}finally{busy=false;}}});
rpc.exports={address(){return fixture.fixture_add.toString();},close(){return life.close();},state(){return life.state();}};
'''
    script=session.create_script(program)
    done=threading.Event();messages=[]
    def message(value,data):
        messages.append(value)
        if value['type']=='error' or value.get('payload',{}).get('kind')=='complete':done.set()
    script.on('message',message);script.load()
    address=script.exports_sync.address()
    child.stdin.write(json.dumps({'address':address})+'\n');child.stdin.flush()
    assert done.wait(5),'owned fixture completion timeout'
    value=json.loads(child.stdout.readline())
    state=script.exports_sync.close()
    print(json.dumps({'messages':messages,'close':state,'child_result':value},indent=2))
    assert state.get('closed') is True and all(state.get(k)==0 for k in ('active_native','callbacks','hooks'))
    assert value=={'value':12}
    script.unload();session.detach()
    assert child.poll() is None,'fixture exited during teardown'
    print(json.dumps({'unload_confirmed':True,'detach_confirmed':True,'fixture_alive':True}))
finally:
    # Only the process spawned above is terminated; no game process is enumerated.
    child.terminate();child.wait(timeout=5)
    if session and not session.is_detached:session.detach()
