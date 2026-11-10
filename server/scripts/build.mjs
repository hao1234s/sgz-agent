import {rmSync} from 'node:fs';
import {resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
const directory=resolve(import.meta.dirname,'..');
// Removed/moved source files must not survive as stale release entry points.
rmSync(resolve(directory,'dist'),{recursive:true,force:true});
const result=spawnSync(process.execPath,[resolve(directory,'node_modules/typescript/bin/tsc'),'-p',resolve(directory,'tsconfig.json')],{cwd:directory,stdio:'inherit'});
if(result.error)throw result.error;
process.exitCode=result.status??1;
