/**
 * KV access. Two kinds of record, both small.
 *
 * `override:{profileUid}` is a fast path only. Status must still work when it
 * is missing, because a lock set from the real Qustodio app never writes it.
 */

export interface OverrideRecord {
  routineUid: string;
  scheduleUid: string;
  /** ISO 8601, Edmonton offset. */
  createdAt: string;
  expiresAt: string;
}

export interface Store {
  getOverride(profileUid: string): Promise<OverrideRecord | null>;
  putOverride(profileUid: string, record: OverrideRecord): Promise<void>;
  deleteOverride(profileUid: string): Promise<void>;
  getCachedStatus<T>(): Promise<T | null>;
  putCachedStatus(value: unknown, ttlSeconds: number): Promise<void>;
}

const overrideKey = (profileUid: string): string => `override:${profileUid}`;

export function kvStore(kv: KVNamespace): Store {
  return {
    async getOverride(profileUid) {
      return await kv.get<OverrideRecord>(overrideKey(profileUid), "json");
    },
    async putOverride(profileUid, record) {
      await kv.put(overrideKey(profileUid), JSON.stringify(record));
    },
    async deleteOverride(profileUid) {
      await kv.delete(overrideKey(profileUid));
    },
    async getCachedStatus<T>() {
      return await kv.get<T>("status", "json");
    },
    async putCachedStatus(value, ttlSeconds) {
      // expirationTtl has a 60 second floor in KV.
      await kv.put("status", JSON.stringify(value), {
        expirationTtl: Math.max(60, ttlSeconds),
      });
    },
  };
}

/** In-memory store, for tests and local runs without a KV binding. */
export function memoryStore(): Store {
  const map = new Map<string, string>();
  return {
    async getOverride(profileUid) {
      const raw = map.get(overrideKey(profileUid));
      return raw ? (JSON.parse(raw) as OverrideRecord) : null;
    },
    async putOverride(profileUid, record) {
      map.set(overrideKey(profileUid), JSON.stringify(record));
    },
    async deleteOverride(profileUid) {
      map.delete(overrideKey(profileUid));
    },
    async getCachedStatus<T>() {
      const raw = map.get("status");
      return raw ? (JSON.parse(raw) as T) : null;
    },
    async putCachedStatus(value) {
      map.set("status", JSON.stringify(value));
    },
  };
}
