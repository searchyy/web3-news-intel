import { Button, Space, Switch, Table, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { SourceRow } from "../types/api";

export function SourcesPage() {
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<SourceRow[]>("/api/admin/sources")
  });
  const patch = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/api/admin/sources/${id}`, {
        method: "PATCH",
        csrf,
        body: JSON.stringify({ enabled })
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] })
  });
  const run = useMutation({
    mutationFn: (id: number) => api(`/api/admin/sources/${id}/run`, { method: "POST", csrf })
  });
  return (
    <>
      <Typography.Title level={3}>数据源</Typography.Title>
      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={[
          { title: "Key", dataIndex: "key" },
          { title: "名称", dataIndex: "name" },
          { title: "适配器", dataIndex: "adapter" },
          { title: "轮询间隔（秒）", dataIndex: "poll_seconds" },
          {
            title: "启用",
            render: (_, row) => (
              <Switch checked={row.enabled} onChange={(enabled) => patch.mutate({ id: row.id, enabled })} />
            )
          },
          {
            title: "操作",
            render: (_, row) => (
              <Space>
                <Button onClick={() => run.mutate(row.id)}>立即运行</Button>
              </Space>
            )
          }
        ]}
      />
    </>
  );
}
