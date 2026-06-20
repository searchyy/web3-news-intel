import { Button, Form, Input, Modal, Space, Table, Tag, message } from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Destination } from "../types/api";

export function FeishuGroupsPage() {
  const [creating, setCreating] = useState(false);
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/api/admin/destinations")
  });
  const mutate = (path: string) =>
    api(path, { method: "POST", csrf }).then(() => queryClient.invalidateQueries({ queryKey: ["destinations"] }));
  const createWebhook = useMutation({
    mutationFn: (values: { key: string; name: string; webhook_url: string }) =>
      api("/api/admin/destinations", {
        method: "POST",
        csrf,
        body: JSON.stringify({ ...values, provider: "feishu_webhook" })
      }),
    onSuccess: () => {
      message.success("已保存，Webhook 不会再次显示");
      setCreating(false);
      queryClient.invalidateQueries({ queryKey: ["destinations"] });
    }
  });
  return (
    <>
      <Button type="primary" className="toolbar" onClick={() => setCreating(true)}>
        添加飞书 Webhook
      </Button>
      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data.filter((item) => item.provider.startsWith("feishu"))}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "模式", dataIndex: "provider" },
          { title: "状态", dataIndex: "status", render: (v) => <Tag>{v}</Tag> },
          { title: "群组", dataIndex: "chat_name" },
          { title: "Secret", dataIndex: "secret_fingerprint" },
          { title: "最近成功", dataIndex: "last_success_at" },
          { title: "最近失败", dataIndex: "last_failure_at" },
          {
            title: "操作",
            render: (_, row) => (
              <Space>
                <Button onClick={() => mutate(`/api/admin/destinations/${row.id}/approve`)}>审批</Button>
                <Button onClick={() => mutate(`/api/admin/destinations/${row.id}/enable`)}>启用</Button>
                <Button onClick={() => mutate(`/api/admin/destinations/${row.id}/disable`)}>禁用</Button>
                <Button onClick={() => mutate(`/api/admin/destinations/${row.id}/test`)}>测试卡片</Button>
              </Space>
            )
          }
        ]}
      />
      <Modal title="添加飞书 Webhook" open={creating} footer={null} onCancel={() => setCreating(false)}>
        <Form layout="vertical" onFinish={(values) => createWebhook.mutate(values)}>
          <Form.Item label="Key" name="key" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="Webhook URL（只写）" name="webhook_url" rules={[{ required: true }]}>
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            保存
          </Button>
        </Form>
      </Modal>
    </>
  );
}
