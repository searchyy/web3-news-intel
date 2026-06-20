import {
  AlertOutlined,
  AuditOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  MessageOutlined,
  SendOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { Layout, Menu, Typography } from "antd";
import { Link, Outlet, useLocation } from "react-router-dom";

const { Header, Sider, Content } = Layout;

export function AdminLayout() {
  const location = useLocation();
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
            { key: "/", icon: <DashboardOutlined />, label: <Link to="/">仪表盘</Link> },
            { key: "/events", icon: <AlertOutlined />, label: <Link to="/events">事件</Link> },
            { key: "/sources", icon: <DatabaseOutlined />, label: <Link to="/sources">来源</Link> },
            {
              key: "/feishu-groups",
              icon: <MessageOutlined />,
              label: <Link to="/feishu-groups">飞书群组</Link>
            },
            { key: "/rules", icon: <ThunderboltOutlined />, label: <Link to="/rules">告警规则</Link> },
            { key: "/deliveries", icon: <SendOutlined />, label: <Link to="/deliveries">投递</Link> },
            { key: "/system", icon: <SettingOutlined />, label: <Link to="/system">系统</Link> },
            { key: "/audit", icon: <AuditOutlined />, label: <Link to="/audit">审计日志</Link> }
          ]}
        />
      </Sider>
      <Layout>
        <Header className="topbar">管理后台</Header>
        <Content className="content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
