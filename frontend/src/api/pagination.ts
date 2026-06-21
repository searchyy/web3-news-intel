import type { PaginatedResponse } from "../types/api";

export function normalizePaginated<T>(
  payload: T[] | PaginatedResponse<T>,
  fallbackPage: number,
  fallbackPageSize: number
): PaginatedResponse<T> {
  if (Array.isArray(payload)) {
    return {
      items: payload,
      total: payload.length,
      page: fallbackPage,
      page_size: fallbackPageSize
    };
  }

  return {
    items: payload.items ?? [],
    total: payload.total ?? 0,
    page: payload.page ?? fallbackPage,
    page_size: payload.page_size ?? fallbackPageSize
  };
}

export function appendPagination(params: URLSearchParams, page: number, pageSize: number) {
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
}
