# 星载载荷共用 CAN 总线 · 间歇性告警审查回放

CAN 2.0A 标准数据帧的逐位回放与故障界定工具。审查员录入至多 **4 个节点**、
各节点初始发送错误计数（TEC）以及至多 **24 条**按时刻排列的标准数据帧请求，
并可对帧标注 **ACK 错误 / CRC 错误 / 指定数据位错误**；页面逐位呈现
SOF、仲裁场、位填充、控制场、数据场、CRC、ACK、EOF、错误标志与帧间隔。

## 功能要点

- **逐位仲裁（CSMA/CR）**：同一总线周期内仅最低标识符获胜并推进；
  负方定位到“首次发送隐性位而总线为显性位”的精确位置（字段 + 字段位 + 线上位序）。
- **逐位轨迹**：SOF → ID → RTR → IDE → r0 → DLC → DATA → CRC-15（多项式
  `0x4599`）→ CRC 界定符 → ACK 槽 → ACK 界定符 → EOF → IFS；
  位填充位（SOF..CRC 连续 5 同电平后插入互补位）单独标注，支持逐位播放/单步。
- **错误界定与故障计数**：
  - 主动错误标志（6 显性）/ 被动错误标志（6 隐性）+ 8 位错误界定符；
  - 被动发送方额外发送 8 位挂起场；
  - 发送检测错误 TEC+8、成功发送 TEC−1；接收方相应 REC+8/−1；
  - TEC/REC ≥ 128 进入 error-passive，TEC ≥ 256 进入 bus-off。
- **bus-off**：节点不再参与后续仲裁、新请求被拒绝并给出恢复进度；
  监测到连续 **128 次 11 位隐性序列**后 TEC/REC 清零、恢复发送资格。
- **字段级校验**：非法帧标识、超出 DLC 的载荷、无效错误位置、
  时刻乱序、节点重名/超限等逐字段反馈；校验失败时不返回任何旧结论。

## 本地运行（无需容器）

```bash
PYTHONPATH=app python3 -m cansim.server --port 8080
# 浏览器访问 http://localhost:8080 ；健康检查 GET /health
```

仅依赖 Python 3.11 标准库。

## Docker Compose

宿主端口通过 `HOST_PORT` 配置（默认 8080）：

```bash
HOST_PORT=9090 docker compose up --build web
```

### 一次性验收服务 `verify`

```bash
docker compose run --build verify
```

该服务依次执行：

1. `compileall` 构建检查；
2. 全量单元测试（仲裁 / CRC-15 / 位填充 / 被动错误 / bus-off 恢复 / 字段校验）；
3. 页面与 `/health` HTTP 冒烟；
4. 三条贯穿代码与页面 API 的可观察场景：
   **正常仲裁**、**被动错误（127+8=135）**、**bus-off 拒绝请求与 128×11 位恢复**；
5. 字段级校验 400 响应检查。

服务执行结束即退出，**退出码即验收结论**（0 通过，非 0 失败）。

本地等价命令：

```bash
PYTHONPATH=app python3 -m cansim.verify
```

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/` | 审查页面 |
| GET | `/health` | 健康响应 `{"status":"ok"}` |
| GET | `/api/config` | 规则常量（节点/请求上限、阈值等） |
| POST | `/api/simulate` | 提交场景，返回逐帧结论与位序轨迹；校验失败返回 400 + `errors[]` |

请求示例：

```json
{
  "nodes": ["SENSOR_A", "SENSOR_B"],
  "initial_tec": {"SENSOR_A": 127},
  "requests": [
    {"time": 0, "node": "SENSOR_A", "id": 256, "dlc": 1, "data": [85],
     "error": {"kind": "ack"}}
  ]
}
```

## 目录结构

```
app/cansim/        # CRC-15、帧结构/位填充、字段校验、总线引擎、HTTP 服务、verify
app/static/        # 审查页面（HTML/CSS/原生 JS）
tests/             # unittest 测试
Dockerfile
docker-compose.yml # web（HOST_PORT 可配置）+ verify（一次性）
```
