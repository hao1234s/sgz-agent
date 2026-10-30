import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';

type Pending = {resolve:(value:any)=>void; reject:(error:Error)=>void; timer:NodeJS.Timeout};

export class WorkerClient {
  private child:ChildProcessWithoutNullStreams;
  private pending=new Map<number,Pending>();
  private nextId=0;
  private closed=false;
  private exited:Promise<void>;

  constructor(command:string,args:string[],cwd:string) {
    this.child=spawn(command,args,{cwd,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
    this.exited=new Promise(resolve=>this.child.once('close',()=>resolve()));
    this.child.on('error',error=>this.fail(error));
    this.child.on('exit',code=>this.fail(new Error(`worker_exited:${code}`)));
    this.child.stdin.on('error',error=>this.fail(error));
    this.child.stderr.on('data',()=>{}); // Drain diagnostics without corrupting JSON-RPC.
    createInterface({input:this.child.stdout}).on('line',line=>{
      try {
        const response=JSON.parse(line);
        const pending=this.pending.get(response.id);
        if(!pending)return;
        clearTimeout(pending.timer);this.pending.delete(response.id);
        if(response.error)pending.reject(new Error(response.error.message));
        else pending.resolve(response.result);
      } catch { this.fail(new Error('invalid_worker_protocol'));this.child.kill(); }
    });
  }

  call(method:string,params:Record<string,unknown>,timeout=120_000):Promise<any> {
    if(this.closed)return Promise.reject(new Error('worker_closed'));
    const id=++this.nextId;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.fail(new Error('worker_timeout'));this.child.kill();},timeout);
      this.pending.set(id,{resolve,reject,timer});
      this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
    });
  }

  private fail(error:Error) {
    this.closed=true;
    for(const request of this.pending.values()){clearTimeout(request.timer);request.reject(error);}
    this.pending.clear();
  }

  async close() {
    this.fail(new Error('worker_closed'));
    this.child.stdin.end();
    const timer=setTimeout(()=>this.child.kill(),1000);
    await this.exited;
    clearTimeout(timer);
  }
}
