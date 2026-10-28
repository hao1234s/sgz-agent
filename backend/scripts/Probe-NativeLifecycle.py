"""Probe the real Frida runtime against an owned disposable process, never the game."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import time
from types import SimpleNamespace

import frida
from sgz_agent.native_session import close_native_session


child_code = '''
import ctypes,json,os,sys,threading,time
library=ctypes.CDLL('msvcrt.dll')
call=library.labs;call.argtypes=[ctypes.c_long];call.restype=ctypes.c_long
stop=threading.Event();threads=[];counts=[0,0,0,0]
def work(index):
    while not stop.is_set():
        assert call(-12)==12
        counts[index]+=1
        time.sleep(.001)
print(json.dumps({'pid':os.getpid(),'address':hex(ctypes.cast(call,ctypes.c_void_p).value)}),flush=True)
for line in sys.stdin:
    command=json.loads(line)
    if command.get('start'):
        threads=[threading.Thread(target=work,args=(i,),daemon=True) for i in range(4)]
        for thread in threads:thread.start()
        print(json.dumps({'started':True}),flush=True)
    elif command.get('status'):
        print(json.dumps({'counts':counts,'alive':all(t.is_alive() for t in threads)}),flush=True)
    elif command.get('quit'):
        stop.set()
        for thread in threads:thread.join()
        break
'''
child = subprocess.Popen([sys._base_executable,'-u','-c',child_code],stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
session=script=None
try:
    child_info=json.loads(child.stdout.readline());pid=child_info['pid']
    assert pid==child.pid
    session=frida.get_local_device().attach(pid)
    helper=(Path(__file__).parents[1]/'src/sgz_agent/native_lifecycle.js').read_text(encoding='utf-8')
    if '--baseline-before-close-gate' in sys.argv:
        helper=helper.replace("          if (closing) return; // A queued entry after detach starts no Lua work.\n",'')
    program='const FIXTURE_ADDRESS='+json.dumps(child_info['address'])+';\n'+helper+r'''
const address=ptr(FIXTURE_ADDRESS);
const life=createNativeLifecycle({attach:(p,h)=>Interceptor.attach(p,h),
 flush:()=>Interceptor.flush(),defer:fn=>setImmediate(fn),publish:value=>send(value)});
const recursive=new NativeFunction(address,'int',['int']);let busy=false;
life.attach(address,{onEnter(){if(busy)return;busy=true;
 try{recursive(2);life.finish({kind:'complete',data:{ok:true}});}finally{busy=false;}}});
rpc.exports={close(){return life.close();},state(){return life.state();}};
'''
    if '--stuck-rpc' in sys.argv:
        program+='\nrpc.exports.close=()=>new Promise(()=>{});\n'
    script=session.create_script(program)
    done=threading.Event();messages=[]
    def message(value,data):
        messages.append(value)
        if value['type']=='error' or value.get('payload',{}).get('kind')=='complete':done.set()
    script.on('message',message);script.load()
    child.stdin.write(json.dumps({'start':True})+'\n');child.stdin.flush()
    assert done.wait(5),'owned fixture completion timeout'
    assert json.loads(child.stdout.readline())=={'started':True}
    with tempfile.TemporaryDirectory(prefix='sgz-owned-probe-') as directory:
        config=SimpleNamespace(data_dir=Path(directory))
        started=time.monotonic()
        if '--stuck-rpc' in sys.argv:
            try:close_native_session(config,session,script,timeout=.05)
            except RuntimeError as error:
                assert 'session_retained' in str(error)
                assert time.monotonic()-started<1
                assert not session.is_detached
                print(json.dumps({'stuck_rpc_bounded':True,'session_retained':True}))
            else:raise AssertionError('stuck RPC was accepted')
        else:
            close_native_session(config,session,script)
            assert session.is_detached
    child.stdin.write(json.dumps({'status':True})+'\n');child.stdin.flush()
    status=json.loads(child.stdout.readline())
    assert status['alive'] and all(n>0 for n in status['counts'])
    assert child.poll() is None,'fixture exited during teardown'
    print(json.dumps({'messages':messages,'fixture_alive':True,'concurrent_calls':status,
                     'detach_confirmed':session.is_detached}))
finally:
    # Only the process spawned above is terminated; no game process is enumerated.
    child.terminate();child.wait(timeout=5)
    if session and not session.is_detached:session.detach()
