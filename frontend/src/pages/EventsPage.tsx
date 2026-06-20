import { Button, Drawer, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EventRow } from "../types/api";

export function EventsPage() {
  const [selected, setSelected] = useState<EventRow | null>(null);
  const { data = [], isLoading } = useQuery({
    queryKey: ["events"],
    queryFn: () => api<EventRow[]>("/api/admin/events?limit=100")
  });
  return (
    <>
      <Typography.Title level={3}>事件列表</Typography.Title>
      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={[
          { title: "标题", dataIndex: "display_title", ellipsis: true, render: (_, row) => row.display_title || row.title },
          { title: "分类", dataIndex: "category_label", render: (_, row) => row.category_label || row.category },
          { title: "级别", dataIndex: "severity_label", render: (_, row) => <Tag>{row.severity_label || row.severity}</Tag> },
          { title: "状态", dataIndex: "status_label", render: (_, row) => row.status_label || row.status },
          { title: "可信度", dataIndex: "trust_score" },
          { title: "发布时间", dataIndex: "published_at" },
          {
            title: "操作",
            render: (_, row) => (
              <Space>
                <Button size="small" onClick={() => setSelected(row)}>
                  详情
                </Button>
                {row.primary_url ? (
                  <Button size="small" href={row.primary_url} target="_blank" rel="noreferrer">
                    原文
                  </Button>
                ) : null}
              </Space>
            )
          }
        ]}
      />
      <Drawer open={!!selected} onClose={() => setSelected(null)} title="事件详情">
        <pre>{JSON.stringify(selected, null, 2)}</pre>
      </Drawer>
    </>
  );
}
