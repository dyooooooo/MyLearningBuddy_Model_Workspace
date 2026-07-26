import type { StudioEventMap, StudioEventName } from "./events";

type Handler<K extends StudioEventName> = (
  detail: StudioEventMap[K],
) => void;

const listeners = new Map<string, Set<EventListener>>();

function toListener<K extends StudioEventName>(handler: Handler<K>): EventListener {
  return ((e: Event) => {
    handler((e as CustomEvent<StudioEventMap[K]>).detail);
  }) as EventListener;
}

export const studioBus = {
  on<K extends StudioEventName>(name: K, handler: Handler<K>): () => void {
    const wrapped = toListener(handler);
    document.addEventListener(name, wrapped);
    let set = listeners.get(name);
    if (!set) {
      set = new Set();
      listeners.set(name, set);
    }
    set.add(wrapped);
    return () => {
      document.removeEventListener(name, wrapped);
      set?.delete(wrapped);
    };
  },

  emit<K extends StudioEventName>(
    name: K,
    detail?: StudioEventMap[K],
  ): void {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  },

  /** Remove all handlers registered via studioBus (tests / hot reload). */
  disposeAll(): void {
    for (const [name, set] of listeners) {
      for (const fn of set) document.removeEventListener(name, fn);
      set.clear();
    }
    listeners.clear();
  },
};
