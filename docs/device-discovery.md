# 如何获取你家的设备地址

每套 DNAKE 安装的设备地址都不同，你需要找到自己的 `dev_no` 和 `dev_ch` 填入 `config.yaml`。

## 方法一：从面板数据库提取（推荐）

DNAKE 面板的 SQLite 数据库中存储了所有设备信息，可以通过 ADB 提取。

### 前置条件

- ADB（Android 调试桥）
- USB 线或 WiFi ADB 连接到面板
- SQLite 浏览器（如 [DB Browser for SQLite](https://sqlitebrowser.org/)）

### 步骤

1. ADB 连接面板：`adb connect <面板IP>:5555`
2. 拷贝数据库：`adb pull /data/data/com.dnake.ifationhome/databases/central.db .`
3. 用 SQLite 浏览器打开 `central.db`，查询 `DEVICE_BEAN` 表
4. 将查询结果填入 `config.yaml`

> 具体的 SQL 查询和字段映射，欢迎 [提 Issue](https://github.com/jupiter2021/smart-home-zigbee/issues) 或联系作者获取帮助。

## 方法二：抓包

如果无法 ADB，可以在同网段用 Wireshark 抓取面板到网关的 TCP 流量（端口 4196），通过操作面板上的按钮逐一识别设备地址。

## 小贴士

- 设备地址在安装时固定，不会变化
- 多个灯可以共享同一个 `dev_no`（同一 Zigbee 模块），通过不同 `dev_ch` 区分
- `zone` 只是分组标签，不影响协议
