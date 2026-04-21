# DNAKE Zigbee Gateway TCP Protocol

DNAKE 智能家居面板通过 TCP 协议与 Zigbee 网关通信，控制灯光、场景、新风、空调、地暖等设备。

## 连接方式

- **协议**：TCP
- **端口**：4196
- **认证**：无（TCP 连接后直接发送数据包）
- **网关发现**：UDP 广播 `255.255.255.255:1092`

## 数据包概览

所有命令均为固定长度的二进制数据包：

| 设备类型 | 数据包长度 | 用途 |
|---------|-----------|------|
| 灯光 (0x01) | 10 字节 | 开/关 |
| 场景 (0x05) | 10 字节 | 触发预设场景 |
| 新风/空调/地暖 (0x08) | 12 字节 | 开/关/调节参数 |

每个数据包以 `0xA9 0x20` 开头（固定 magic），以校验和结尾（所有前置字节求和 & 0xFF）。

> 完整的字节级协议细节已封装在 `smart_home_zigbee/protocol.py` 中，直接使用库即可，无需手动构建数据包。
>
> 如果你在做自己的实现或有深入的协议问题，欢迎 [提 Issue 讨论](https://github.com/jupiter2021/smart-home-zigbee/issues) 或联系作者。

## 支持的命令

| 命令 | 说明 |
|------|------|
| ON / OFF | 开关灯光、新风、空调、地暖等设备 |
| SCENE_EXECUTE | 触发网关硬件场景 |
| TEMP | 设置温度（空调/地暖，16-32°C） |
| MODE | 设置空调模式（制冷/制热/送风/除湿） |
| WIND_SPEED | 设置风速（新风：低/中/高；空调：低/中/高/自动） |
| READ_FULL_STATUS | 读取空调完整状态（开关/风速/模式/温度） |
| READ_ROOM_TEMP | 读取空调室温传感器 |
| READ_SWITCH | 读取地暖开关状态 |
| READ_SET_TEMP | 读取地暖设定温度 |
| DIM | 调光（调光模块） |

## 扩展设备通道类型 (chType)

12 字节扩展包的第 5 个字节是 chType，用于区分同一 devNo/devCh 下的不同设备：

| chType | 设备 | 说明 |
|--------|------|------|
| 0x19 | 空调 | 开关/温度/模式/风速/状态查询 |
| 0x59 | 新风 | 开关/风速 |
| 0xF1 | 地暖 | 开关/温度 |

## 快速验证

用 `nc` 可以直接向网关发命令，验证连通性：

```bash
# 开客厅主灯（需要替换为你的设备地址和校验和）
printf '\xa9\x20\x01\x51\x00\x02\x01\x00\x00\x1e' | nc -w 2 192.168.71.12 4196
```

> 设备地址因安装不同而不同，获取方法见 [device-discovery.md](device-discovery.md)。

## 协议来源

协议通过分析 DNAKE 面板通信流量获得，关键调用链：

```
LightShortcutView.onClick()
  → LightShortcutVM.control()
    → LinkHelper.ctrlLight()
      → TcpLink.sendTcp() → TCP gateway:4196
```
