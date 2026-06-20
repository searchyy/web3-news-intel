import { Button, Card, Form, Input, Space, Switch, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { FeishuConfig, FeishuTestResult } from "../types/api";

const statusMap = {
  connected: { color: "green", text: "连接成功" },
  failed: { color: "red", text: "连接失败" },
  not_tested: { color: "gold", text: "未测试" }
} as const;

export function FeishuSettingsPage() {
  const [form] = Form.useForm<FeishuConfig>();
  const [status, setStatus] = useState<FeishuConfig["connection_status"]>("not_tested");
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["feishu-config"],
    queryFn: () => api<FeishuConfig>("/api/admin/system/feishu-config")
  });
  useEffect(() => {
    if (data) {
      form.setFieldsValue(data);
      setStatus(data.connection_status);
    }
  }, [data, form]);
  const save = useMutation({
    mutationFn: (values: FeishuConfig) =>
      api<FeishuConfig>("/api/admin/system/feishu-config", {
        method: "POST",
        csrf,
        body: JSON.stringify(values)
      }),
    onSuccess: (result) => {
      form.setFieldsValue(result);
      message.success("飞书配置已保存，敏感字段不会明文回显");
      queryClient.invalidateQueries({ queryKey: ["feishu-config"] });
    }
  });
  const test = useMutation({
    mutationFn: () =>
      api<FeishuTestResult>("/api/admin/destinations/test-feishu", {
        method: "POST",
        csrf
      }),
    onSuccess: (result) => {
      setStatus(result.status === "success" ? "connected" : "failed");
      if (result.status === "success") {
        message.success(result.message || "连接成功");
      } else {
        message.error(result.error || "连接失败");
      }
    }
  });
  const currentStatus = statusMap[status];
  return (
    <>
      <Typography.Title level={3}>飞书配置</Typography.Title>
      <Card
        title="企业应用机器人"
        extra={<Tag color={currentStatus.color}>{currentStatus.text}</Tag>}
        loading={isLoading}
      >
        <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values as FeishuConfig)}>
          <Form.Item label="FEISHU_APP_ID" name="FEISHU_APP_ID" rules={[{ required: true, message: "请输入 App ID" }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="FEISHU_APP_SECRET" name="FEISHU_APP_SECRET" rules={[{ required: true, message: "请输入 App Secret 或保留已掩码值" }]}>
            <Input.Password autoComplete="new-password" placeholder="••••••" />
          </Form.Item>
          <Form.Item label="FEISHU_VERIFICATION_TOKEN" name="FEISHU_VERIFICATION_TOKEN">
            <Input.Password autoComplete="off" placeholder="••••••" />
          </Form.Item>
          <Form.Item label="FEISHU_ENCRYPT_KEY" name="FEISHU_ENCRYPT_KEY">
            <Input.Password autoComplete="off" placeholder="••••••" />
          </Form.Item>
          <Form.Item label="FEISHU_TEST_CHAT_ID" name="FEISHU_TEST_CHAT_ID" rules={[{ required: true, message: "请输入测试群 Chat ID" }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Space size="large">
            <Form.Item label="FEISHU_ENABLED" name="FEISHU_ENABLED" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
            <Form.Item label="FEISHU_SEND_ENABLED" name="FEISHU_SEND_ENABLED" valuePropName="checked">
              <Switch checkedChildren="允许发送" unCheckedChildren="禁止发送" />
            </Form.Item>
          </Space>
          <Space>
            <Button type="primary" htmlType="submit" loading={save.isPending}>
              保存配置
            </Button>
            <Button onClick={() => test.mutate()} loading={test.isPending}>
              测试连接
            </Button>
          </Space>
        </Form>
      </Card>
    </>
  );
}
