import readline from 'node:readline';
readline.createInterface({input:process.stdin}).on('line', line => {
  const request=JSON.parse(line);
  if(request.method==='exit') process.exit(2);
  if(request.method==='hang') return;
  if(request.method==='stderr') process.stderr.write('diagnostic only\n');
  setTimeout(()=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result:{echo:request.params}})+'\n'), request.params.delay??0);
});
