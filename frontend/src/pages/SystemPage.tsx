import { Card, Descriptions, Skeleton, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

const labels: Record<string, string> = {
  api: "API",
  postgresql: "PostgreSQL",
  redis: "Redis",
  celery: "Celery"
};

export function SystemPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["system-health"],
    queryFn: () => api<Record<string, string>>("/api/admin/system/health")
  });
  if (isLoading || !data) {
    return <Skeleton active />;
  }
  return (
    <>
      <Typography.Title level={3}>系统设置</Typography.Title>
      <Card title="系统状态">
        <Descriptions
          bordered
          items={Object.entries(data).map(([key, value]) => ({
            key,
            label: labels[key] || key,
            children: value
          }))}
        />
      </Card>
    </>
  );
}
