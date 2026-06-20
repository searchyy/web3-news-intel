import { Card, Descriptions, Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function SystemPage() {
  const { data, isLoading } = useQuery({ queryKey: ["system-health"], queryFn: () => api<Record<string, string>>("/api/admin/system/health") });
  if (isLoading || !data) {
    return <Skeleton active />;
  }
  return (
    <Card title="系统状态">
      <Descriptions bordered items={Object.entries(data).map(([key, value]) => ({ key, label: key, children: value }))} />
    </Card>
  );
}
