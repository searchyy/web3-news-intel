import { Button, Drawer, Space, Table, Tag } from "antd";
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
      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={[
          { title: "标题", dataIndex: "title", ellipsis: true },
          { title: "分类", dataIndex: "category" },
          { title: "级别", dataIndex: "severity", render: (v) => <Tag>{v}</Tag> },
          { title: "状态", dataIndex: "status" },
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
