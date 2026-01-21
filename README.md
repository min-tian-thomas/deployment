# 部署描述与配置生成工具（MVP）

这个仓库用于 **本地描述部署拓扑**，并通过一个小工具在本机生成应用的配置和运行目录。

当前实现处于 MVP 阶段，目标是：

- 统一描述二进制版本及 Tag；
- 描述机房 / 主机的 CPU、NUMA 和网卡拓扑；
- 描述每台机器上要跑哪些应用、使用哪些 CPU、绑定哪些网卡；
- 一次性在本机生成可运行的应用目录（包含配置文件 + 指向二进制的 symlink）。

---

## 目录结构

```text
deployment/
  binaries/
    requirements.yaml           # 二进制版本 + tag 的统一定义
    md_server/                  # md_server 的各版本（生成或从 LFS 拉取）
      v1.2.3/
        md_server
    md_recorder/                # md_recorder 的各版本
      v1.3.0/
        md_recorder

  deploy/
    idc_shanghai/
      hosts.yaml                # 每台机器的 CPU/NUMA 拓扑 + 网卡信息
      deployments.yaml          # 每台机器、每个应用的部署计划
      schedules.yaml            # 交易时间窗口（目前仅结构定义，尚未被消费）
      templates/
        dce_md_publisher.json   # publisher 的 JSON 配置模板
        dce_md_recorder.json    # recorder 的 JSON 配置模板

  install/                      # 这是一个生成目录，当时我们也提交让git托管用于记录变更历史
    idc_shanghai/
      host01/
        dce_md_publisher/       # 为 host01 生成的 publisher 运行目录
          dce_md_publisher.json # 渲染后的配置
          dce_md_publisher      # symlink，指向具体版本的 md_server
        dce_md_recorder/        # 为 host01 生成的 recorder 运行目录
          dce_md_recorder.json
          dce_md_recorder       # symlink，指向具体版本的 md_recorder

  tools/
    gen_config.py               # 配置生成与校验脚本

  Makefile                      # make binaries / make config 等辅助命令
  requirements.txt              # Python 依赖
```

---

## YAML 结构说明

### 1. `binaries/requirements.yaml`

示例：

```yaml
md_server:
  tags:
    prod: v1.2.3
    staging: v1.2.4-rc1
  required_versions:
    - v1.2.3
    - v1.2.4-rc1

md_recorder:
  tags:
    prod: v1.3.0
    staging: v1.3.0
  required_versions:
    - v1.3.0
```

- 顶层 key 是二进制名称（如 `md_server`、`md_recorder`）。
- `tags`：业务友好的 tag 映射到具体版本号。
- `required_versions`：当前需要准备的所有版本列表。
  - `make binaries` 会为这些版本生成（或校验存在）`binaries/<name>/<version>/<name>`；
  - 不在 `required_versions` 中的旧版本目录会被自动清理。

---

### 2. `deploy/idc_shanghai/hosts.yaml`

示例：

```yaml
host01:
  cpus: 16
  numa_nodes:
    - id: 0
      cpus: 0-7
    - id: 1
      cpus: 8-15
  isolated_cpus: 2-15
  shared_cpus: 0,1
  nics:
    - name: eth0
      ip: 192.168.1.100
      type: ethernet
    - name: sf0
      ip: 192.168.1.101
      type: solarflare
```

- 顶层 key 为主机名（如 `host01`）。
- `numa_nodes`：推荐写法，用于计算 `cpu -> numa_node` 映射。
- `isolated_cpus`：为 busy spin（如主业务 loop）保留的独占 CPU 集合。
- `shared_cpus`：可共享的 CPU 集合（如日志、管理线程）。
- `nics`：机器上的网卡及其 IP 信息。

---

### 3. `deploy/idc_shanghai/deployments.yaml`

顶层是 **host → apps** 的映射。示例：

```yaml
host01:
  dce_md_recorder:
    binary: md_recorder
    tag: prod
    templates:
      - name: dce_md_recorder.json
        cfg_envs:
          log_cpu: 0          # numa 注释由工具根据 hosts.yaml 自动刷新
          main_loop_cpu: 2    # busy spin，必须在 isolated_cpus 内，且在同一 host 上不可与其他 app 重复
          admin_loop_cpu: 1

  dce_md_publisher:
    binary: md_server
    tag: prod
    templates:
      - name: dce_md_publisher.json
        cfg_envs:
          log_cpu: 0          # shared_cpus 中的核
          main_loop_cpu: 3    # isolated_cpus 中的核，且跨 app 不可重复
          admin_loop_cpu: 1
          listen_nic: sf0     # 网卡名称，IP 注释由工具自动刷新
          listen_port: 12800
```

约定与校验规则：

- 顶层 key 为 host 名称（如 `host01`）。
- host 下的 key 为应用名（如 `dce_md_publisher`、`dce_md_recorder`）。
- 每个应用：
  - `binary` / `tag`（或 `version`）指定要使用的二进制和版本；
  - `templates` 是一个列表，每个元素对应一个配置模板：
    - `name`：模板文件名，对应 `deploy/<dc>/templates/<name>`；
    - `cfg_envs`：该模板的环境参数（字典）。

CPU / NUMA 相关约束：

- `main_loop_cpu`：
  - 必须落在 `isolated_cpus` 范围内；
  - **同一 host 上所有应用之间不允许复用** 同一个 `main_loop_cpu`（避免 busy spin 抢占）；
- `log_cpu`：必须落在 `shared_cpus` 范围内；
- `admin_loop_cpu`：必须在 `[0, cpus)` 范围内；
- 三个 CPU 字段之间不允许重复。

注释自动刷新：

- 脚本会在生成配置前，根据 `hosts.yaml` 重新写回：
  - `listen_nic` 行尾的 `# <ip>` 注释；
  - 所有 `*_cpu` 行尾的 `# numa <id>` 注释；
- 因此手工修改这些注释会在下次 `make config` 时被覆盖。

跨应用引用：

- 在 recorder 模板中可以引用 publisher 的 env，例如：
  - `{{dce_md_publisher.listen_nic}}`
  - `{{dce_md_publisher.listen_port}}`
- 工具会在同一 host 下查找 `dce_md_publisher` 的配置，从其 `cfg_envs` 中取 `listen_nic` / `listen_port`，并：
  - 用 `hosts.yaml` 把 `listen_nic` 转换为 IP；
  - 将结果注入到模板渲染中。

---

### 4. `deploy/idc_shanghai/schedules.yaml`

示例：

```yaml
defaults:
  timezone: Asia/Shanghai

schedules:
  dce_md_publisher:
    rules:
      - sessions:
          - ["09:00", "15:10"] # Day Session
          - ["21:00", "04:10"] # Night Session (to the next day)
        days: "mon-fri"
```

- 用于描述应用的交易时间窗口和规则；
- 当前脚本尚未消费该文件，仅保留结构，未来可以扩展为生成 cron / systemd 定时配置等。

---

### 5. 模板示例

以 `deploy/idc_shanghai/templates/dce_md_publisher.json` 为例：

```json
{
  "logging": {
    "log_level": "Info",
    "log_cpu": "{{log_cpu}}"
  },
  "event_loops": [
    {
      "name": "main_loop",
      "cpu_id": "{{main_loop_cpu}}",
      "busy_spin": true
    },
    {
      "name": "admin_loop",
      "cpu_id": "{{admin_loop_cpu}}",
      "busy_spin": false
    }
  ],
  "listen_nic": "{{listen_nic}}",
  "listen_port": "{{listen_port}}"
}
```

`tools/gen_config.py` 使用简单的字符串替换把 `{{...}}` 填成具体数值，并在写盘前做一次 JSON 语法校验。

recorder 的模板 `dce_md_recorder.json` 类似，只是会额外包含连接 publisher 的字段：

```json
"connect_host": "{{dce_md_publisher.listen_nic}}",
"connect_port": "{{dce_md_publisher.listen_port}}"
```

---

## `tools/gen_config.py` 行为概览

脚本对每个 `dc/host/app` 主要做以下几步：

1. 从 `deploy/<dc>/hosts.yaml` 读取主机拓扑（CPU 数量、NUMA、isolated/shared CPU、网卡等）。
2. 从 `deploy/<dc>/deployments.yaml` 读取该 host 上的应用列表及其模板配置。
3. 从 `binaries/requirements.yaml` 读取二进制定义，解析 `binary` + `tag`/`version`：
   - 校验 tag 映射出来的版本是否在 `required_versions` 内；
   - 解析出目标路径 `binaries/<binary>/<version>/<binary>`。
4. 对每个模板执行 CPU 相关校验（见上文）。
5. 计算 `cpu -> numa_node` 映射，并在生成时把每个使用到的 CPU 的 NUMA 信息打印到 stdout，方便肉眼确认。
6. 渲染模板：
   - 把 `{{log_cpu}}` / `{{main_loop_cpu}}` / `{{admin_loop_cpu}}` 等替换为具体数值；
   - 如果模板中使用了 `{{listen_nic}}`，则从 `cfg_envs.listen_nic` 和 `hosts.yaml` 解析出 IP 并替换；
   - 支持 recorder 通过 `{{dce_md_publisher.*}}` 引用 publisher 的 listen_nic / listen_port。
7. 校验渲染结果是否为合法 JSON。
8. 将每个模板渲染后的结果写入：

   ```text
   install/<dc>/<host>/<app>/<template_name>
   ```

9. 创建二进制 symlink：

   ```text
   install/<dc>/<host>/<app>/<app_name> -> ../../../../binaries/<binary>/<version>/<binary>
   ```

---

## Makefile 用法

当前仓库提供了一些基于 `uv` 的辅助命令，MVP 核心使用方式：

```bash
# （可选）从 requirements.in 刷新 requirements.txt
make requirements

# （可选）为 tools/ 创建隔离虚拟环境并安装依赖
make venv

# 1）根据 binaries/requirements.yaml 准备 / 清理二进制目录
make binaries

# 2）为所有 dc/host/app 生成配置与应用目录
make config
```

- `make binaries` 会调用 `tools/gen_config.py binaries`，对 `binaries/requirements.yaml` 中定义的每个 binary：
  - 为所有 `required_versions` 生成（或校验存在）mock 二进制 `binaries/<name>/<version>/<name>`；
  - 删除不在 `required_versions` 中的旧版本目录。

- `make config` 会调用 `tools/gen_config.py`（无参数），扫描所有 `deploy/<dc>/deployments.yaml` 和 `hosts.yaml`，对每个 `dc/host/app`：
  - 打印使用到的 CPU 与 NUMA 映射；
  - 生成配置文件到 `install/<dc>/<host>/<app>/`；
  - 为应用创建指向正确版本二进制的 symlink。

---

## 后续可扩展方向

- 利用 `schedules.yaml` 生成 cron / systemd 定时任务配置；
- 支持更多应用类型和更复杂的模板占位符；
- 增强跨应用依赖描述（不仅 recorder 连接 publisher）；
- 与远端部署系统对接（目前仅支持本地生成，不涉及远程分发）。
