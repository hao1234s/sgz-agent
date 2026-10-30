import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {WorkerClient} from '../core/worker.js';
import type {ResolvedConfig} from '../core/config.js';
export function createWorker(config:ResolvedConfig):WorkerClient {
  const legacy=resolve(config.rootDir,'backend/config/game.local.json');
  const configPath=config.client.configPath??(existsSync(legacy)?legacy:resolve(config.rootDir,'backend/config/client/game.local.json'));
  return new WorkerClient(config.client.pythonPath,
    ['-m','sgz_agent.client.cli','serve','--config',configPath],config.rootDir);
}
