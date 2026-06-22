import { Button, Table, Typography } from "antd";
import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { normalizePaginated } from "../api/pagination";
import { useAuth } from "../auth/AuthContext";
import type { Delivery, PaginatedResponse } from "../types/api";

export function DeliveriesPage() {
  const { csrf } = useAuth();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const queryClient = useQueryClient();
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["deliveries", page, pageSize],
    queryFn: async () => {
      const payload = await api<Delivery[] | PaginatedResponse<Delivery>>(
        `/api/admin/deliveries?page=${page}&page_size=${pageSize}`
      );
      return normalizePaginated(payload, page, pageSize);
    },
    placeholderData: keepPreviousData,
    staleTime: 30_000
  });
  const retry = useMutation({
    mutationFn: (id: number) => api(`/api/admin/deliveries/${id}/retry`, { method: "POST", csrf }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["deliveries"] })
  });
  return (
    <>
      <Typography.Title level={3}>投递记录</Typography.Title>
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
          { title: "ID", dataIndex: "id" },
          { title: "事件", dataIndex: "event_id" },
          { title: "目标", dataIndex: "target" },
          { title: "状态", dataIndex: "status" },
          { title: "尝试次数", dataIndex: "attempts" },
          { title: "HTTP", dataIndex: "response_status" },
          { title: "错误", dataIndex: "last_error", ellipsis: true },
          { title: "操作", render: (_, row) => <Button onClick={() => retry.mutate(row.id)}>重试</Button> }
        ]}
      />
    </>
  );
}
