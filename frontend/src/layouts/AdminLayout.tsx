import {
  AlertOutlined,
  AuditOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  MessageOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { Button, Layout, Menu, Space, Typography } from "antd";
import { Link, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const { Header, Sider, Content } = Layout;

export function AdminLayout() {
  const location = useLocation();
  const { logout } = useAuth();
  return (
    <Layout className="app-shell">
      <Sider width={248} breakpoint="lg" collapsedWidth={0}>
        <Typography.Title className="brand" level={4}>
          Web3 News Intel
        </Typography.Title>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={[
            { key: "/", icon: <DashboardOutlined />, label: <Link to="/">控制台</Link> },
            { key: "/events", icon: <AlertOutlined />, label: <Link to="/events">事件列表</Link> },
            { key: "/sources", icon: <DatabaseOutlined />, label: <Link to="/sources">数据源</Link> },
            {
              key: "/feishu-groups",
              icon: <MessageOutlined />,
              label: <Link to="/feishu-groups">飞书群组</Link>
            },
            { key: "/rules", icon: <ThunderboltOutlined />, label: <Link to="/rules">告警规则</Link> },
            { key: "/deliveries", icon: <SendOutlined />, label: <Link to="/deliveries">投递记录</Link> },
            {
              key: "/settings/feishu",
              icon: <SettingOutlined />,
              label: <Link to="/settings/feishu">飞书配置</Link>
            },
            {
              key: "/settings/ai",
              icon: <RobotOutlined />,
              label: <Link to="/settings/ai">AI 智能整理</Link>
            },
            { key: "/system", icon: <SettingOutlined />, label: <Link to="/system">系统设置</Link> },
            { key: "/audit", icon: <AuditOutlined />, label: <Link to="/audit">审计日志</Link> }
          ]}
        />
      </Sider>
      <Layout>
        <Header className="topbar">
          <Space>
            <span>Web3 新闻情报管理后台</span>
            <Button size="small" onClick={() => void logout()}>
              退出登录
            </Button>
          </Space>
        </Header>
        <Content className="content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
