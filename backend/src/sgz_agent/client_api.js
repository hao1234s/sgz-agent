// Fixed read-only operations. Runs on the game's Lua call thread, restoring its stack.
const client = Process.getModuleByName('S3Client.exe');
if (client.path.replaceAll('\\', '/').toLowerCase() !== OPTIONS.path) throw Error('api_game_path_mismatch');
const lifecycle = createNativeLifecycle({
  attach:(address,handler)=>Interceptor.attach(address,handler),
  flush:()=>Interceptor.flush(), defer:fn=>setImmediate(fn), publish:message=>send(message)
});
rpc.exports = {close:()=>lifecycle.close(),state:()=>lifecycle.state()};
function fn(n, r, a) { return new NativeFunction(client.getExportByName(n), r, a); }
const top = fn('lua_gettop', 'int', ['pointer']);
const checkstack = fn('lua_checkstack', 'int', ['pointer', 'int']);
const settop = fn('lua_settop', 'void', ['pointer', 'int']);
const field = fn('lua_getfield', 'int', ['pointer', 'int', 'pointer']);
const pushvalue = fn('lua_pushvalue', 'void', ['pointer', 'int']);
const pushinteger = fn('lua_pushinteger', 'void', ['pointer', 'int64']);
const pushstring = fn('lua_pushstring', 'pointer', ['pointer', 'pointer']);
const call = fn('lua_pcallk', 'int', ['pointer', 'int', 'int', 'int', 'int64', 'pointer']);
const type = fn('lua_type', 'int', ['pointer', 'int']);
const string = fn('lua_tolstring', 'pointer', ['pointer', 'int', 'pointer']);
const number = fn('lua_tonumberx', 'double', ['pointer', 'int', 'pointer']);
const pushnil = fn('lua_pushnil', 'void', ['pointer']);
const next = fn('lua_next', 'int', ['pointer', 'int']);
const pointer = fn('lua_topointer', 'pointer', ['pointer', 'int']);
const cfunction = fn('lua_tocfunction', 'pointer', ['pointer', 'int']);
const upvalue = fn('lua_getupvalue', 'pointer', ['pointer', 'int', 'int']);
const names = new Map();
function key(name) { if (!names.has(name)) names.set(name, Memory.allocUtf8String(name)); return names.get(name); }
function get(L, i, name) {
  if (i !== -1001000 && type(L, i) !== 5) throw Error('api_table_unavailable:' + name);
  return field(L, i, key(name));
}
function scalar(L, i) {
  if (type(L, i) === 3) return number(L, i, ptr(0));
  if (type(L, i) === 4) return string(L, i, ptr(0)).readUtf8String().slice(0, 200);
  return null;
}
function value(L, i, name) { get(L, i, name); const v = scalar(L, -1); settop(L, top(L) - 1); return v; }
function module(L, name) {
  if (get(L, -1001000, '_LOADED') !== 5 || get(L, -1, name) !== 5) throw Error('api_module_unavailable:' + name);
  return top(L);
}
function invoke(L, manager, name, coordinates) {
  if (get(L, manager, name) !== 6) throw Error('api_method_unavailable:' + name);
  pushvalue(L, manager);
  for (const c of coordinates || []) pushinteger(L, c);
  if (call(L, 1 + (coordinates || []).length, 1, 0, 0, ptr(0)) !== 0) throw Error('api_read_failed:' + name);
  return top(L);
}
function identity(L) {
  const saved = top(L), p = module(L, 'logic.player.player_mgr');
  const role = String(value(L, p, 'player_id'));
  if (get(L, p, 'cur_server') !== 5) throw Error('api_login_required');
  const s = top(L), server = value(L, s, 'server_id'), region = value(L, s, 'name'), season = value(L, s, 'season');
  settop(L, saved);
  if (role !== OPTIONS.role_id || server !== OPTIONS.server_id || region !== OPTIONS.region || season !== OPTIONS.season)
    throw Error('api_identity_mismatch');
  return {role_id: role, server_id: server, region, season};
}
let busy = false, initialized = false, finished = false, cursor = 0, lastStep = 0, responseTime = 0;
const responses = new Map(), responseTypes = new Map();
function fail(e) { if (!finished) lifecycle.finish({kind: 'failure', reason: String(e.message || e)}); finished = true; }
function monster(L, entity) {
  const id = value(L, entity, 'id');
  if (id === null) return null; // Empty zeibing info means no personal roaming bandit.
  if (get(L, entity, 'monster') !== 5) throw Error('api_monster_schema_mismatch');
  const m = top(L), result = {id};
  for (const name of ['conf_id', 'army_conf_id', 'end_time', 'use_code', 'troop_type']) result[name] = value(L, m, name);
  if (get(L, m, 'pos') !== 5) throw Error('api_monster_position_missing');
  result.x = value(L, top(L), 'row'); result.y = value(L, top(L), 'col');
  if (!Number.isInteger(result.x) || !Number.isInteger(result.y)) throw Error('api_monster_position_invalid');
  settop(L, entity);
  return result;
}
function decodeResult(L, index, name) {
  if (type(L, index) !== 5) throw Error('api_response_schema_mismatch');
  const items = [];
  if (get(L, index, name === 'cs_get_shanzhai_data' ? 'infos' : 'info') !== 5) throw Error('api_response_list_missing');
  const list = top(L);
  if (name === 'cs_get_zeibing_data') { const item = monster(L, list); if (item) items.push(item); }
  else {
    pushnil(L);
    while (next(L, list) !== 0) {
      if (items.length >= 500 || type(L, -1) !== 5) throw Error('api_response_list_invalid');
      const item = monster(L, top(L)); if (!item) throw Error('api_monster_id_missing');
      items.push(item); settop(L, top(L) - 1);
    }
  }
  responses.set(name, items); responseTime = Date.now();
}
function setup(L) {
  get(L, -1001000, '_LOADED'); const loaded = top(L);
  get(L, loaded, 'network.network'); const factory = top(L);
  if (type(L, factory) !== 6) throw Error('api_network_unavailable');
  let found = false;
  for (let n = 1; n <= 40; n++) {
    const p = upvalue(L, factory, n); if (p.isNull()) break;
    if (p.readUtf8String() === 'Global') { found = true; break; } settop(L, factory);
  }
  if (!found) throw Error('api_network_unavailable');
  get(L, -1, 'network'); get(L, -1, 'rpc'); get(L, -1, 'v_server_proto'); get(L, -1, '__cobj'); const schema = top(L);
  get(L, loaded, 'sproto.core'); const core = top(L);
  for (const [name, expected] of [['cs_get_shanzhai_data', 925], ['cs_get_zeibing_data', 922]]) {
    get(L, core, 'protocol'); pushvalue(L, schema); pushstring(L, key(name));
    if (call(L, 2, 3, 0, 0, ptr(0)) !== 0 || scalar(L, -3) !== expected || type(L, -1) !== 2)
      throw Error('api_protocol_version_mismatch');
    responseTypes.set(pointer(L, -1).toString(), name); settop(L, core);
  }
  const hooked = new Set();
  for (const name of ['decode', 'decodep', 'decoden']) {
    get(L, core, name); const address = cfunction(L, -1); settop(L, core);
    if (address.isNull()) throw Error('api_codec_unavailable');
    if (hooked.has(address.toString())) continue; hooked.add(address.toString());
    lifecycle.attach(address, {
      onEnter(args) { this.L = args[0]; this.name = responseTypes.get(pointer(this.L, 1).toString()); },
      onLeave(ret) {
        if (!this.name || finished) return;
        const state = this.L, saved = top(state), count = ret.toInt32();
        try {
          if (!checkstack(state, 32)) throw Error('api_lua_stack_capacity');
          if (count < 1 || count > 3) throw Error('api_decode_failed');
          decodeResult(state, saved - count + 1, this.name);
        }
        catch (e) { fail(e); } finally { settop(state, saved); }
      }
    });
  }
  const npc = module(L, 'logic.map.npc.map_npc_mgr');
  for (const method of ['send_get_shanzhai_data', 'send_get_zeibing_data']) { invoke(L, npc, method); settop(L, npc); }
}
function bandits(L) {
  const items = [...responses.values()].flat();
  const npc = module(L, 'logic.map.npc.map_npc_mgr');
  // Config labels are read only from matching IDs, after the existing response handler runs.
  if (get(L, npc, 'uid_npc_data') !== 5) throw Error('api_npc_cache_unavailable');
  const cache = top(L); pushnil(L);
  let count = 0;
  while (next(L, cache) !== 0) {
    if (++count > 10000) throw Error('api_npc_cache_too_large');
    const item = items.find(i => String(i.id) === String(scalar(L, -2)));
    if (item && type(L, -1) === 5) {
      const entry = top(L);
      if (value(L, entry, 'conf_id') === item.conf_id && get(L, entry, 'config') === 5) {
        item.name = value(L, top(L), 'show_name') || value(L, top(L), 'name');
        item.level = value(L, top(L), 'level');
      }
      settop(L, entry);
    }
    settop(L, top(L) - 1);
  }
  return items.filter(i => i.x >= OPTIONS.x0 && i.x <= OPTIONS.x1 && i.y >= OPTIONS.y0 && i.y <= OPTIONS.y1);
}
lifecycle.attach(client.getExportByName('lua_pcallk'), {onEnter(args) {
  if (busy || finished || Date.now() - lastStep < 16) return;
  busy = true; lastStep = Date.now(); const L = args[0], saved = top(L);
  try {
    if (!checkstack(L, 64)) throw Error('api_lua_stack_capacity');
    const who = identity(L);
    if (!initialized) { setup(L); initialized = true; settop(L, saved); }
    const height = OPTIONS.y1 - OPTIONS.y0 + 1, total = (OPTIONS.x1 - OPTIONS.x0 + 1) * height;
    if (cursor < total) {
      const manager = module(L, 'logic.map.map_mgr'), items = [];
      for (let n = 0; n < 64 && cursor < total; n++, cursor++) {
        const x = OPTIONS.x0 + Math.floor(cursor / height), y = OPTIONS.y0 + cursor % height;
        const result = invoke(L, manager, 'get_grid_res_by_pos', [x, y]), item = {x, y};
        if (type(L, result) === 5) for (const name of ['id', 'name', 'level', 'land_type']) item[name] = value(L, result, name);
        items.push(item); settop(L, manager);
      }
      send({kind: 'chunk', items});
    }
    if (cursor === total && responses.size === 2 && Date.now() - responseTime >= 100) {
      settop(L, saved); const found = bandits(L);
      settop(L, saved); identity(L);
      lifecycle.finish({kind: 'complete', identity: who, bandits: found}); finished = true;
    }
  } catch (e) { fail(e); }
  finally { settop(L, saved); busy = false; }
}});
