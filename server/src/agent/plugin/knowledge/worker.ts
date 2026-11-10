import {WorkerClient} from '../core/worker.js';
import type {ResolvedConfig} from '../core/config.js';
export function createWorker(config:ResolvedConfig):WorkerClient {
  return new WorkerClient(config.knowledge.pythonPath,
    ['-m','sgz_agent.knowledge.cli','serve','--directory',config.knowledge.directory],config.rootDir);
}
