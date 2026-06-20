import { Button, Table } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Delivery } from "../types/api";

export function DeliveriesPage() {
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const { data = [] } = useQuery({ queryKey: ["deliveries"], queryFn: () => api<Delivery[]>("/api/admin/deliveries") });
  const retry = useMutation({
    mutationFn: (id: number) => api(`/api/admin/deliveries/${id}/retry`, { method: "POST", csrf }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["deliveries"] })
  });
  return (
    <Table rowKey="id" dataSource={data} columns={[
      { title: "ID", dataIndex: "id" },
      { title: "事件", dataIndex: "event_id" },
      { title: "目标", dataIndex: "target" },
      { title: "状态", dataIndex: "status" },
      { title: "尝试", dataIndex: "attempts" },
      { title: "HTTP", dataIndex: "response_status" },
      { title: "错误", dataIndex: "last_error", ellipsis: true },
      { title: "操作", render: (_, row) => <Button onClick={() => retry.mutate(row.id)}>重试</Button> }
    ]} />
  );
}
