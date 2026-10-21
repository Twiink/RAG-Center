import axios from "axios";

import { clearAuthError, notifyAuthError } from "@/lib/authError";
import type { ApiErrorResponse } from "@/types/api";

export const UNAUTHORIZED_CODE = 20010;

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
  timeout: 120000,
});

http.interceptors.request.use((config) => {
  const apiKey = import.meta.env.API_KEY?.trim();
  if (apiKey) {
    config.headers.Authorization = `Bearer ${apiKey}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => {
    const code = response.data?.code;
    if (code === UNAUTHORIZED_CODE) {
      notifyAuthError();
      return Promise.reject(new Error(response.data?.msg || "未授权"));
    }
    if (code === 0) {
      clearAuthError();
    }
    return response;
  },
  (error) => {
    if (axios.isAxiosError<ApiErrorResponse>(error)) {
      if (error.response?.status === 401) notifyAuthError();
      const message = error.response?.data?.msg || error.message;
      const failure = new Error(message);
      return Promise.reject(failure);
    }
    return Promise.reject(error);
  },
);

if (import.meta.env.DEV && !import.meta.env.API_KEY?.trim()) {
  console.warn("[rag-center] Frontend/.env 未配置 API_KEY，后端 AUTH_ENABLED=true 时请求会失败");
}
