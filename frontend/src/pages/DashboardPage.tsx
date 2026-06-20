import ReactECharts from "echarts-for-react";
import { Card, Col, Row, Skeleton, Statistic } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DashboardSummary } from "../types/api";

export function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api<DashboardSummary>("/api/admin/dashboard/summary")
  });
  if (isLoading || !data) {
    return <Skeleton active />;
  }
  const chart = {
    tooltip: {},
    xAxis: { type: "category", data: ["1小时", "24小时", "关键", "成功投递", "失败投递"] },
    yAxis: { type: "value" },
    series: [
      {
        type: "bar",
        data: [
          data.events_last_hour,
          data.events_last_24h,
          data.critical_high_count,
          data.successful_deliveries,
          data.failed_deliveries
        ]
      }
    ]
  };
  return (
    <>
      <Row gutter={[12, 12]}>
        <Metric title="最近1小时事件" value={data.events_last_hour} />
        <Metric title="最近24小时事件" value={data.events_last_24h} />
        <Metric title="Critical/High" value={data.critical_high_count} />
        <Metric title="启用来源" value={data.enabled_sources} />
        <Metric title="失败来源" value={data.failed_sources} />
        <Metric title="待审批飞书群" value={data.pending_feishu_groups} />
      </Row>
      <Card className="section" title="事件与投递概览">
        <ReactECharts option={chart} style={{ height: 320 }} />
      </Card>
    </>
  );
}

function Metric({ title, value }: { title: string; value: number }) {
  return (
    <Col xs={24} md={8} xl={4}>
      <Card>
        <Statistic title={title} value={value} />
      </Card>
    </Col>
  );
}
