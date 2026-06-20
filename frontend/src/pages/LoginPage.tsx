import { Button, Card, Form, Input, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  return (
    <main className="login-page">
      <Card className="login-card">
        <Typography.Title level={3}>管理员登录</Typography.Title>
        <Form
          layout="vertical"
          onFinish={async (values) => {
            try {
              await auth.login(values.username, values.password);
              navigate("/");
            } catch {
              message.error("用户名或密码错误");
            }
          }}
        >
          <Form.Item label="用户名" name="username" rules={[{ required: true }]}>
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            登录
          </Button>
        </Form>
      </Card>
    </main>
  );
}
