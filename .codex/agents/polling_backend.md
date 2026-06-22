# polling_backend

后台轮询 Agent。文件所有权：

- app/scheduler/
- app/workers/
- app/fetch/
- source 调度、队列、增量抓取和相关测试

不得绕过来源访问控制、403、429、Retry-After 或 robots 限制。
