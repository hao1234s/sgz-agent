import {apply} from '../../dist/index.js';
const tools=[];
await apply({tools:{register:t=>tools.push(t.name)},skills:{snapshot:async()=>({skills:[],complete:true})},
  effect:()=>{},systemPrompt:{section:()=>{}}},
  {rootDir:process.cwd(),capabilities:JSON.parse(process.env.SGZ_CAPABILITIES),autoStart:false});
console.log(JSON.stringify(tools));
