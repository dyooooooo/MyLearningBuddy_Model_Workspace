import { studioFetch, type HttpMethod } from "./http";

export type MinerUModelVersion = "pipeline" | "vlm" | "MinerU-HTML";

export interface MinerUSettingsPublic {
  api_base_url: string;
  model_version: MinerUModelVersion;
  language: string;
  enable_formula: boolean;
  enable_table: boolean;
  is_ocr: boolean;
  timeout_s: number;
  updated_at: string;
}

export interface MinerUSettingsOut {
  settings: MinerUSettingsPublic;
  api_token_set: boolean;
  api_token: string;
}

export interface MinerUSettingsUpdate {
  api_token?: string;
  api_base_url?: string;
  model_version?: MinerUModelVersion;
  language?: string;
  enable_formula?: boolean;
  enable_table?: boolean;
  is_ocr?: boolean;
  timeout_s?: number;
}

export interface MinerUTestOut {
  ok: boolean;
  message: string;
}

const BASE = "/api/v1/studio/settings/mineru";

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  return studioFetch<T>(url, { method: method as HttpMethod, body });
}

export const mineruSettings = {
  get(): Promise<MinerUSettingsOut> {
    return request<MinerUSettingsOut>("GET", BASE);
  },

  save(body: MinerUSettingsUpdate): Promise<MinerUSettingsOut> {
    return request<MinerUSettingsOut>("PUT", BASE, body);
  },

  test(body: MinerUSettingsUpdate = {}): Promise<MinerUTestOut> {
    return request<MinerUTestOut>("POST", `${BASE}/test`, body);
  },
};
