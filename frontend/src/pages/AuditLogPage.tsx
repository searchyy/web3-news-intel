import { Table, Typography } from "antd";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { normalizePaginated } from "../api/pagination";
import type { AuditLog, PaginatedResponse } from "../types/api";

export function AuditLogPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["audit-logs", page, pageSize],
    queryFn: async () => {
      const payload = await api<AuditLog[] | PaginatedResponse<AuditLog>>(
        `/api/admin/audit-logs?page=${page}&page_size=${pageSize}`
      );
      return normalizePaginated(payload, page, pageSize);
    },
    placeholderData: keepPreviousData,
    staleTime: 30_000
  });
  return (
    <>
      <Typography.Title level={3}>审计日志</Typography.Title>
      <Table
        rowKey="id"
        loading={isLoading || isFetching}
        dataSource={data?.items ?? []}
        pagination={{
          current: data?.page ?? page,
          pageSize: data?.page_size ?? pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          onChange: (nextPage, nextPageSize) => {
            setPage(nextPage);
            setPageSize(nextPageSize);
          }
        }}
        columns={[
          { title: "时间", dataIndex: "created_at" },
          { title: "管理员", dataIndex: "admin_subject" },
          { title: "动作", dataIndex: "action" },
          { title: "资源", dataIndex: "resource_type" },
          { title: "请求 ID", dataIndex: "request_id" }
        ]}
      />
    </>
  );
}
