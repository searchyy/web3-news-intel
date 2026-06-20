import { Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AuditLog } from "../types/api";

export function AuditLogPage() {
  const { data = [] } = useQuery({
    queryKey: ["audit-logs"],
    queryFn: () => api<AuditLog[]>("/api/admin/audit-logs")
  });
  return (
    <>
      <Typography.Title level={3}>审计日志</Typography.Title>
      <Table
        rowKey="id"
        dataSource={data}
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
