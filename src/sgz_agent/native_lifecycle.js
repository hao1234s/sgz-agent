// Native listeners are drained before publishing a terminal result or unloading.
// Kept independent of Frida globals so callback ordering is executable in tests.
function createNativeLifecycle(api) {
  const listeners = [], waiters = [];
  let active = 0, callbacks = 0, closing = false, closed = false;
  let scheduled = false, detached = false, terminal = null;
  function state() {
    return {closing, closed, active_native:active, callbacks, hooks:listeners.length};
  }
  function schedule() {
    if (scheduled || closed) return;
    scheduled = true;
    api.defer(pump);
  }
  function pump() {
    scheduled = false;
    if (!closing || active || callbacks || closed) return;
    if (!detached) {
      while (listeners.length) listeners.pop().detach();
      api.flush();
      detached = true;
      schedule(); // Leave this JS/native boundary before acknowledging quiescence.
      return;
    }
    closed = true;
    if (terminal) api.publish({...terminal, lifecycle:state()});
    for (const resolve of waiters.splice(0)) resolve(state());
  }
  return {
    state,
    attach(address, handler) {
      if (closing) throw Error('native_lifecycle_closing');
      const ticket = '_sgzNativeTracked' + listeners.length;
      const listener = api.attach(address, {
        onEnter(args) {
          this[ticket] = false;
          if (closing) return; // A queued entry after detach starts no Lua work.
          this[ticket] = true;
          active++; callbacks++;
          try { if (!closing && handler.onEnter) handler.onEnter.call(this,args); }
          finally { callbacks--; schedule(); }
        },
        onLeave(retval) {
          if (this[ticket] !== true) return;
          this[ticket] = false;
          callbacks++;
          try { if (!closing && handler.onLeave) handler.onLeave.call(this,retval); }
          finally { active--; callbacks--; schedule(); }
        }
      });
      listeners.push(listener);
      return listener;
    },
    finish(message) {
      if (closing) return;
      terminal = message; closing = true; schedule();
    },
    close() {
      if (closed) return Promise.resolve(state());
      closing = true; schedule();
      return new Promise(resolve => waiters.push(resolve));
    }
  };
}
