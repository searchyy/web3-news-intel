import { Button, Form, Input, Select, Switch, Table, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Destination, Rule } from "../types/api";

const severityOptions = [
  { value: "low", label: "低" },
  { value: "normal", label: "普通" },
  { value: "high", label: "高" },
  { value: "critical", label: "严重" }
];

const modeOptions = [
  { value: "immediate", label: "立即发送" },
  { value: "digest", label: "摘要发送" }
];

export function RulesPage() {
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const { data: rules = [] } = useQuery({ queryKey: ["rules"], queryFn: () => api<Rule[]>("/api/admin/rules") });
  const { data: destinations = [] } = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/api/admin/destinations")
  });
  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api("/api/admin/rules", { method: "POST", csrf, body: JSON.stringify(values) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rules"] })
  });
  return (
    <>
      <Typography.Title level={3}>告警规则</Typography.Title>
      <Form
        layout="inline"
        className="toolbar"
        onFinish={(values) =>
          create.mutate({
            ...values,
            categories: [],
            sources: [],
            symbols: [],
            chains: [],
            timezone: values.timezone || "UTC",
            maximum_messages_per_hour: Number(values.maximum_messages_per_hour || 30)
          })
        }
      >
        <Form.Item name="destination_id" rules={[{ required: true, message: "请选择通知目标" }]}>
          <Select placeholder="通知目标" style={{ width: 220 }} options={destinations.map((d) => ({ value: d.id, label: d.name }))} />
        </Form.Item>
        <Form.Item name="name" rules={[{ required: true, message: "请输入规则名称" }]}>
          <Input placeholder="规则名称" />
        </Form.Item>
        <Form.Item name="minimum_severity" initialValue="normal">
          <Select style={{ width: 130 }} options={severityOptions} />
        </Form.Item>
        <Form.Item name="delivery_mode" initialValue="immediate">
          <Select style={{ width: 130 }} options={modeOptions} />
        </Form.Item>
        <Form.Item name="maximum_messages_per_hour" initialValue={30}>
          <Input type="number" placeholder="每小时上限" />
        </Form.Item>
        <Form.Item name="critical_bypass_quiet_hours" valuePropName="checked" initialValue={false}>
          <Switch checkedChildren="严重绕过" unCheckedChildren="不绕过" />
        </Form.Item>
        <Button htmlType="submit" type="primary">
          创建
        </Button>
      </Form>
      <Table
        rowKey="id"
        dataSource={rules}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "最低级别", dataIndex: "minimum_severity" },
          { title: "模式", dataIndex: "delivery_mode" },
          { title: "时区", dataIndex: "timezone" },
          { title: "每小时上限", dataIndex: "maximum_messages_per_hour" },
          { title: "启用", dataIndex: "enabled", render: (value) => (value ? "是" : "否") }
        ]}
      />
    </>
  );
}
