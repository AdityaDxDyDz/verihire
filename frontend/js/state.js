// Minimal pub/sub store and a storage wrapper that never throws (private windows, blocked storage, quota).

export function createStore(initial = {}) {
  let state = { ...initial };
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      for (const fn of [...listeners]) fn(state);
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export function safeStorage(getStorage) {
  const memory = new Map();
  const backend = () => {
    try {
      return getStorage();
    } catch {
      return null;
    }
  };
  return {
    get(key) {
      try {
        const b = backend();
        if (b) return b.getItem(key);
      } catch { /* fall through to memory */ }
      return memory.has(key) ? memory.get(key) : null;
    },
    set(key, value) {
      try {
        const b = backend();
        if (b) {
          b.setItem(key, value);
          return;
        }
      } catch { /* fall through to memory */ }
      memory.set(key, value);
    },
    remove(key) {
      try {
        backend()?.removeItem(key);
      } catch { /* ignore */ }
      memory.delete(key);
    },
  };
}
