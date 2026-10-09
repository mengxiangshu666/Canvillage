// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type {
  CanvasMutationOutboxStatusSnapshot,
  CanvasMutationScope,
  CanvasMutationTransaction,
} from "./canvasMutationTypes";

export const CANVAS_MUTATION_OUTBOX_STATUS_EVENT =
  "village-canvas:mutation-outbox-status";

const DB_NAME = "village-canvas-mutation-outbox";
const DB_VERSION = 1;
const STORE_NAME = "transactions";
const RETENTION_MS = 7 * 24 * 60 * 60 * 1_000;
const MAX_SCOPE_ENTRIES = 200;
const TERMINAL_STATES = new Set(["committed", "rolled_back"]);

export interface CanvasMutationOutbox {
  put(transaction: CanvasMutationTransaction): Promise<void>;
  get(transactionId: string): Promise<CanvasMutationTransaction | null>;
  delete(transactionId: string): Promise<void>;
  listScope(scope: CanvasMutationScope): Promise<CanvasMutationTransaction[]>;
  clear(): Promise<void>;
  prune(now?: number): Promise<void>;
  getStatus(): CanvasMutationOutboxStatusSnapshot;
}

export interface RecoverableCanvasMutationOutbox extends CanvasMutationOutbox {
  recover(): Promise<void>;
  dispose(): void;
}

function storageErrorCode(error: unknown): string {
  if (typeof DOMException !== "undefined" && error instanceof DOMException && error.name) {
    return error.name;
  }
  if (error instanceof Error && error.name) return error.name;
  return "storage_error";
}

function emitOutboxStatus(status: CanvasMutationOutboxStatusSnapshot): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(CANVAS_MUTATION_OUTBOX_STATUS_EVENT, {
    detail: status,
  }));
}

function scopeKey(scope: CanvasMutationScope): string {
  return `${scope.projectId}\u0000${scope.canvasId}`;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
    transaction.onerror = () => reject(transaction.error ?? new Error("IndexedDB transaction failed"));
  });
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      const store = db.objectStoreNames.contains(STORE_NAME)
        ? request.transaction!.objectStore(STORE_NAME)
        : db.createObjectStore(STORE_NAME, { keyPath: "transactionId" });
      if (!store.indexNames.contains("scopeKey")) {
        store.createIndex("scopeKey", "scopeKey", { unique: false });
      }
      if (!store.indexNames.contains("updatedAt")) {
        store.createIndex("updatedAt", "updatedAt", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB open failed"));
  });
}

type StoredCanvasMutationTransaction = CanvasMutationTransaction & { scopeKey: string };

function stored(transaction: CanvasMutationTransaction): StoredCanvasMutationTransaction {
  return { ...transaction, scopeKey: scopeKey(transaction) };
}

function exposed(transaction: StoredCanvasMutationTransaction): CanvasMutationTransaction {
  const { scopeKey: _storedScopeKey, ...value } = transaction;
  return value;
}

export function createMemoryCanvasMutationOutbox(): CanvasMutationOutbox {
  const values = new Map<string, CanvasMutationTransaction>();
  const status: CanvasMutationOutboxStatusSnapshot = {
    status: "degraded",
    storage: "memory",
    pendingCount: 0,
    updatedAt: Date.now(),
  };
  const publish = () => {
    status.pendingCount = values.size;
    status.updatedAt = Date.now();
    emitOutboxStatus({ ...status });
  };
  return {
    async put(transaction) {
      values.set(transaction.transactionId, transaction);
      publish();
    },
    async get(transactionId) {
      return values.get(transactionId) ?? null;
    },
    async delete(transactionId) {
      values.delete(transactionId);
      publish();
    },
    async listScope(scope) {
      return [...values.values()]
        .filter((item) => item.projectId === scope.projectId && item.canvasId === scope.canvasId)
        .sort((left, right) => left.createdAt - right.createdAt);
    },
    async clear() {
      values.clear();
      publish();
    },
    async prune(now = Date.now()) {
      const cutoff = now - RETENTION_MS;
      for (const [transactionId, transaction] of values) {
        if (transaction.updatedAt < cutoff && TERMINAL_STATES.has(transaction.state)) {
          values.delete(transactionId);
        }
      }
      const grouped = new Map<string, CanvasMutationTransaction[]>();
      for (const transaction of values.values()) {
        const key = scopeKey(transaction);
        grouped.set(key, [...(grouped.get(key) ?? []), transaction]);
      }
      for (const transactions of grouped.values()) {
        const overflow = transactions
          .sort((left, right) => right.updatedAt - left.updatedAt)
          .filter((transaction) => TERMINAL_STATES.has(transaction.state))
          .slice(MAX_SCOPE_ENTRIES);
        overflow.forEach((transaction) => values.delete(transaction.transactionId));
      }
      publish();
    },
    getStatus: () => ({ ...status }),
  };
}

export function createIndexedDbCanvasMutationOutbox(): CanvasMutationOutbox {
  let databasePromise: Promise<IDBDatabase> | null = null;
  const database = () => {
    databasePromise ??= openDatabase().catch((error) => {
      // An opening failure can be temporary (private mode, quota, browser
      // lifecycle). Do not cache a rejected promise forever.
      databasePromise = null;
      throw error;
    });
    return databasePromise;
  };

  const all = async (): Promise<StoredCanvasMutationTransaction[]> => {
    const db = await database();
    const transaction = db.transaction(STORE_NAME, "readonly");
    const completed = transactionComplete(transaction);
    const result = await requestResult(
      transaction.objectStore(STORE_NAME).getAll() as IDBRequest<StoredCanvasMutationTransaction[]>,
    );
    await completed;
    return result;
  };

  return {
    async put(value) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readwrite");
      const completed = transactionComplete(transaction);
      transaction.objectStore(STORE_NAME).put(stored(value));
      await completed;
    },
    async get(transactionId) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readonly");
      const completed = transactionComplete(transaction);
      const result = await requestResult(
        transaction.objectStore(STORE_NAME).get(transactionId) as IDBRequest<
          StoredCanvasMutationTransaction | undefined
        >,
      );
      await completed;
      return result ? exposed(result) : null;
    },
    async delete(transactionId) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readwrite");
      const completed = transactionComplete(transaction);
      transaction.objectStore(STORE_NAME).delete(transactionId);
      await completed;
    },
    async listScope(scope) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readonly");
      const completed = transactionComplete(transaction);
      const index = transaction.objectStore(STORE_NAME).index("scopeKey");
      const result = await requestResult(
        index.getAll(IDBKeyRange.only(scopeKey(scope))) as IDBRequest<StoredCanvasMutationTransaction[]>,
      );
      await completed;
      return result.map(exposed).sort((left, right) => left.createdAt - right.createdAt);
    },
    async clear() {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readwrite");
      const completed = transactionComplete(transaction);
      transaction.objectStore(STORE_NAME).clear();
      await completed;
    },
    async prune(now = Date.now()) {
      const cutoff = now - RETENTION_MS;
      const values = await all();
      const grouped = new Map<string, StoredCanvasMutationTransaction[]>();
      const remove = new Set<string>();
      for (const value of values) {
        if (value.updatedAt < cutoff && TERMINAL_STATES.has(value.state)) {
          remove.add(value.transactionId);
        }
        grouped.set(value.scopeKey, [...(grouped.get(value.scopeKey) ?? []), value]);
      }
      for (const transactions of grouped.values()) {
        transactions
          .sort((left, right) => right.updatedAt - left.updatedAt)
          .filter((value) => TERMINAL_STATES.has(value.state))
          .slice(MAX_SCOPE_ENTRIES)
          .forEach((value) => remove.add(value.transactionId));
      }
      if (remove.size === 0) return;
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readwrite");
      const completed = transactionComplete(transaction);
      const store = transaction.objectStore(STORE_NAME);
      remove.forEach((transactionId) => store.delete(transactionId));
      await completed;
    },
    getStatus: () => ({
      status: "healthy",
      storage: "indexeddb",
      pendingCount: 0,
      updatedAt: Date.now(),
    }),
  };
}

export function createResilientCanvasMutationOutbox(
  indexed: CanvasMutationOutbox,
  options: { listenForOnline?: boolean } = {},
): RecoverableCanvasMutationOutbox {
  const fallbackValues = new Map<string, CanvasMutationTransaction>();
  const deletedIds = new Set<string>();
  let clearRequested = false;
  let pruneRequestedAt: number | null = null;
  let status: CanvasMutationOutboxStatusSnapshot = {
    status: "healthy",
    storage: "indexeddb",
    pendingCount: 0,
    updatedAt: Date.now(),
  };
  let operationTail: Promise<void> = Promise.resolve();

  const publish = (next: Partial<CanvasMutationOutboxStatusSnapshot> = {}) => {
    status = {
      ...status,
      ...next,
      pendingCount: fallbackValues.size,
      updatedAt: Date.now(),
    };
    emitOutboxStatus({ ...status });
  };

  const fallbackList = (scope: CanvasMutationScope) => [...fallbackValues.values()]
    .filter((item) => item.projectId === scope.projectId && item.canvasId === scope.canvasId)
    .sort((left, right) => left.createdAt - right.createdAt);

  const enqueue = <T>(action: () => Promise<T>): Promise<T> => {
    const result = operationTail.catch(() => undefined).then(action);
    operationTail = result.then(() => undefined, () => undefined);
    return result;
  };

  const setDegraded = (error: unknown) => {
    publish({
      status: "degraded",
      storage: "memory",
      lastError: storageErrorCode(error),
    });
  };

  const recoverIndexedDb = async (): Promise<void> => {
    if (status.storage === "indexeddb") return;
    try {
      if (clearRequested) {
        await indexed.clear();
      } else {
        for (const transactionId of deletedIds) {
          await indexed.delete(transactionId);
        }
      }
      if (pruneRequestedAt !== null) {
        await indexed.prune(pruneRequestedAt);
      }
      for (const transaction of fallbackValues.values()) {
        await indexed.put(transaction);
      }
      // Probe the backing store before declaring recovery complete. A replay
      // with no pending values must not hide a still-broken IndexedDB handle.
      await indexed.get("__village-canvas-outbox-healthcheck__");
      clearRequested = false;
      deletedIds.clear();
      pruneRequestedAt = null;
      publish({ status: "healthy", storage: "indexeddb", lastError: undefined });
    } catch (error) {
      setDegraded(error);
    }
  };

  const onOnline = () => {
    void enqueue(recoverIndexedDb);
  };
  const listenForOnline = options.listenForOnline ?? true;
  if (listenForOnline && typeof window !== "undefined") {
    window.addEventListener("online", onOnline);
  }

  return {
    async put(value) {
      await enqueue(async () => {
        fallbackValues.set(value.transactionId, value);
        deletedIds.delete(value.transactionId);
        if (status.storage === "memory") {
          await recoverIndexedDb();
        } else {
          try {
            await indexed.put(value);
          } catch (error) {
            setDegraded(error);
          }
        }
        publish();
      });
    },
    get: (id) => enqueue(async () => {
      if (status.storage === "memory") await recoverIndexedDb();
      if (status.storage === "memory") return fallbackValues.get(id) ?? null;
      try {
        return await indexed.get(id) ?? fallbackValues.get(id) ?? null;
      } catch (error) {
        setDegraded(error);
        return fallbackValues.get(id) ?? null;
      }
    }),
    async delete(id) {
      await enqueue(async () => {
        fallbackValues.delete(id);
        deletedIds.add(id);
        if (status.storage === "memory") {
          await recoverIndexedDb();
        } else {
          try {
            await indexed.delete(id);
            deletedIds.delete(id);
          } catch (error) {
            setDegraded(error);
          }
        }
        publish();
      });
    },
    listScope: (scope) => enqueue(async () => {
      if (status.storage === "memory") await recoverIndexedDb();
      if (status.storage === "indexeddb") {
        try {
        const indexedValues = await indexed.listScope(scope);
        const merged = new Map(indexedValues.map((item) => [item.transactionId, item] as const));
        for (const item of fallbackList(scope)) merged.set(item.transactionId, item);
        return [...merged.values()].sort((left, right) => left.createdAt - right.createdAt);
        } catch (error) {
          setDegraded(error);
        }
      }
      return fallbackList(scope);
    }),
    async clear() {
      await enqueue(async () => {
        fallbackValues.clear();
        deletedIds.clear();
        clearRequested = true;
        if (status.storage === "memory") {
          await recoverIndexedDb();
        } else {
          try {
            await indexed.clear();
            clearRequested = false;
          } catch (error) {
            setDegraded(error);
          }
        }
        publish();
      });
    },
    async prune(now) {
      await enqueue(async () => {
        const pruneAt = now ?? Date.now();
        const cutoff = pruneAt - RETENTION_MS;
        for (const [id, value] of fallbackValues) {
          if (value.updatedAt < cutoff && TERMINAL_STATES.has(value.state)) {
            fallbackValues.delete(id);
          }
        }
        pruneRequestedAt = pruneAt;
        if (status.storage === "memory") {
          await recoverIndexedDb();
        } else {
          try {
            await indexed.prune(pruneAt);
            pruneRequestedAt = null;
          } catch (error) {
            setDegraded(error);
          }
        }
        publish();
      });
    },
    getStatus: () => ({ ...status, pendingCount: fallbackValues.size }),
    recover: () => enqueue(recoverIndexedDb),
    dispose() {
      if (listenForOnline && typeof window !== "undefined") {
        window.removeEventListener("online", onOnline);
      }
    },
  };
}

export function createDefaultCanvasMutationOutbox(): CanvasMutationOutbox {
  if (typeof indexedDB === "undefined") return createMemoryCanvasMutationOutbox();
  return createResilientCanvasMutationOutbox(createIndexedDbCanvasMutationOutbox());
}
