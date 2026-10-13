import assert from "node:assert/strict";
import test from "node:test";
import { inkDebugOn, setInkDebug, stampInkDebug, syncInkDebugButton } from "./inkDebug.ts";

const store = new Map<string, string>();
const memory: Storage = {
  get length() {
    return store.size;
  },
  clear() {
    store.clear();
  },
  getItem(key: string) {
    return store.has(key) ? store.get(key)! : null;
  },
  key(index: number) {
    return [...store.keys()][index] ?? null;
  },
  removeItem(key: string) {
    store.delete(key);
  },
  setItem(key: string, value: string) {
    store.set(key, value);
  },
};

Object.defineProperty(globalThis, "localStorage", { configurable: true, value: memory });

test("inkDebugOn defaults to on", () => {
  store.clear();
  assert.equal(inkDebugOn(), true);
});

test("setInkDebug persists off then on", () => {
  store.clear();
  setInkDebug(false);
  assert.equal(inkDebugOn(), false);
  setInkDebug(true);
  assert.equal(inkDebugOn(), true);
});

test("stampInkDebug marks host only when off", () => {
  store.clear();
  const host = { classList: { toggle: (name: string, on?: boolean) => last = [name, on] } };
  let last: [string, boolean | undefined] | null = null;
  setInkDebug(false);
  stampInkDebug(host as HTMLElement);
  assert.deepEqual(last, ["is-ink-plain", true]);
  setInkDebug(true);
  stampInkDebug(host as HTMLElement);
  assert.deepEqual(last, ["is-ink-plain", false]);
});

test("syncInkDebugButton reflects pressed state", () => {
  const attrs = new Map<string, string>();
  const cls = new Set<string>();
  const btn = {
    classList: {
      toggle(name: string, on?: boolean) {
        if (on) cls.add(name);
        else cls.delete(name);
      },
    },
    setAttribute(name: string, value: string) {
      attrs.set(name, value);
    },
    title: "",
  } as unknown as HTMLButtonElement;
  syncInkDebugButton(btn, true);
  assert.equal(cls.has("is-active"), true);
  assert.equal(attrs.get("aria-pressed"), "true");
  syncInkDebugButton(btn, false);
  assert.equal(cls.has("is-active"), false);
  assert.equal(attrs.get("aria-pressed"), "false");
});
