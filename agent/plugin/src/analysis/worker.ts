import {WorkerClient} from '../core/worker.js';
import type {ResolvedConfig} from '../core/config.js';
export function createWorker(config:ResolvedConfig):WorkerClient {
  return new WorkerClient(config.analysis.pythonPath,['-m','sgz_agent.analysis.cli','serve'],config.rootDir);
}
