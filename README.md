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
  deployments/
    required_binaries.yaml      # 二进制版本 + tag 的统一定义

    idc_shanghai/
      hosts.yaml                # 每台机器的 CPU/NUMA 拓扑 + 网卡信息
      deployments.yaml          # 每台机器、每个应用的部署计划
      templates/
        dce_md_publisher.json
        dce_md_recorder.json
        dce_md_recorder_backup.json

  install/                      # 生成目录（包含 binaries + 每个 app 的运行目录）
    binaries/
      md_server/
        v1.2.3/
          md_server
      md_recorder/
        v1.3.0/
          md_recorder
    idc_shanghai/
      host01/
        dce_md_publisher/       # 为 host01 生成的 publisher 运行目录
          dce_md_publisher.json # 渲染后的配置
          dce_md_publisher      # symlink，指向具体版本的 md_server
        dce_md_recorder/        # 为 host01 生成的 recorder 运行目录
          dce_md_recorder.json
          dce_md_recorder       # symlink，指向具体版本的 md_recorder
      host02/
        dce_md_recorder_backup/
          dce_md_recorder_backup.json
          dce_md_recorder_backup

  tools/
    gen_config.py               # 配置生成与校验脚本

  Makefile                      # make binaries / make config 等辅助命令
  requirements.txt              # Python 依赖
```

---

## YAML 结构说明

### 1. `deployments/required_binaries.yaml`

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
  - `make binaries` 会为这些版本生成（或校验存在）`install/binaries/<name>/<version>/<name>`；
  - 不在 `required_versions` 中的旧版本目录会被自动清理。

---

### 2. `deployments/<dc>/hosts.yaml`

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

### 3. `deployments/<dc>/deployments.yaml`

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
    - `name`：模板文件名，对应 `deployments/<dc>/templates/<name>`；
    - `cfg_envs`：该模板的环境参数（字典）。

CPU / NUMA 相关约束：

- `main_loop_cpu`：
  - 必须落在 `isolated_cpus` 范围内；
  - **同一 host 上所有应用之间不允许复用** 同一个 `main_loop_cpu`（避免 busy spin 抢占）；
- `log_cpu`：必须落在 `shared_cpus` 范围内；
- `admin_loop_cpu`：必须落在 `shared_cpus` 范围内；
- 三个 CPU 字段之间不允许重复。

注释自动刷新：

- 脚本会在生成配置前，根据 `hosts.yaml` 重新写回：
  - `listen_nic` 行尾的 `# <ip>` 注释；
  - 所有 `*_cpu` 行尾的 `# numa <id>` 注释；
- 因此手工修改这些注释会在下次 `make config` 时被覆盖。

跨应用引用：

- 在任意模板中可以引用其它应用的 cfg_envs：
  - `{{OtherApp.some_key}}`
- 工具会在所有 `deployments/<dc>/deployments.yaml` 中构建全局索引（要求 application name 全局唯一），找到 `OtherApp` 所在的 `(dc, host)` 并读取其 cfg_envs。
- 对 `listen_nic` 会使用目标 host 的 `hosts.yaml` 将网卡名转换为 IP 后注入。
- 任何 key 名包含 `shm` 的跨应用引用被认为是共享内存相关：必须满足引用方与被引用方在同一台机器上，否则 `make config` 会报错。

---

### 4. 模板示例

以 `deployments/idc_shanghai/templates/dce_md_publisher.json` 为例：

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

recorder 的模板可以包含跨应用引用，例如：

```json
"connect_host": "{{dce_md_publisher.listen_nic}}",
"connect_port": "{{dce_md_publisher.listen_port}}"
```

---

## `tools/gen_config.py` 行为概览

脚本对每个 `dc/host/app` 主要做以下几步：

1. 从 `deployments/<dc>/hosts.yaml` 读取主机拓扑（CPU 数量、NUMA、isolated/shared CPU、网卡等）。
2. 从 `deployments/<dc>/deployments.yaml` 读取该 host 上的应用列表及其模板配置。
3. 从 `deployments/required_binaries.yaml` 读取二进制定义，解析 `binary` + `tag`/`version`：
   - 校验 tag 映射出来的版本是否在 `required_versions` 内；
   - 解析出目标路径 `install/binaries/<binary>/<version>/<binary>`。
4. 对每个模板执行 CPU 相关校验（见上文）。
5. 计算 `cpu -> numa_node` 映射，并在生成时把每个使用到的 CPU 的 NUMA 信息打印到 stdout，方便肉眼确认。
6. 渲染模板：
   - 把 `{{log_cpu}}` / `{{main_loop_cpu}}` / `{{admin_loop_cpu}}` 等替换为具体数值；
   - 如果模板中使用了 `{{listen_nic}}`，则从 `cfg_envs.listen_nic` 和 `hosts.yaml` 解析出 IP 并替换；
   - 支持任意模板通过 `{{OtherApp.key}}` 引用其它应用的 cfg_envs。
7. 校验渲染结果是否为合法 JSON。
8. 若 host 设置了 `log_dir`，则强制写入 `logging.log_dir=<log_dir>/<app_name>`。
9. 将每个模板渲染后的结果写入：

   ```text
   install/<dc>/<host>/<app>/<template_name>
   ```

10. 创建二进制 symlink：

   ```text
   install/<dc>/<host>/<app>/<app_name> -> ../../../binaries/<binary>/<version>/<binary>
   ```

---

## Makefile 用法

当前仓库提供了一些基于 `uv` 的辅助命令，MVP 核心使用方式：

```bash
# （可选）从 requirements.in 刷新 requirements.txt
make requirements

# （可选）为 tools/ 创建隔离虚拟环境并安装依赖
make venv

# 1）根据 deployments/required_binaries.yaml 准备 / 清理二进制目录
make binaries

# 2）为所有 dc/host/app 生成配置与应用目录
make config
```

- `make binaries` 会调用 `tools/gen_binaries.py`，对 `deployments/required_binaries.yaml` 中定义的每个 binary：
  - 为所有 `required_versions` 生成（或校验存在）mock 二进制 `install/binaries/<name>/<version>/<name>`；
  - 删除不在 `required_versions` 中的旧版本目录。

- `make config` 会调用 `tools/gen_config.py`（无参数），扫描所有 `deployments/<dc>/deployments.yaml` 和 `hosts.yaml`，对每个 `dc/host/app`：
  - 打印使用到的 CPU 与 NUMA 映射；
  - 生成配置文件到 `install/<dc>/<host>/<app>/`；
  - 为应用创建指向正确版本二进制的 symlink。

---

## 约束与校验规则（非常重要）

以下规则由 `make config`/`tools/gen_config.py` 强制校验或强制注入，违反时会直接报错退出。

- 应用名（application name）必须全局唯一
  - 同一个仓库内，所有 `deployments/<dc>/deployments.yaml` 中出现的应用名不允许重复。
  - 这是为了支持 `{{OtherApp.key}}` 的跨应用引用。

- CPU 分配与 busy-spin 规则
  - `main_loop_cpu` 必须属于 `isolated_cpus`。
  - 同一台 host 上，不同应用的 busy-spin CPU 不允许复用（避免 busy-spin 互相抢占）。
  - `log_cpu` 与 `admin_loop_cpu` 必须属于 `shared_cpus`。
  - 三个 CPU（log/main/admin）不允许重复，且必须在 `[0, cpus)` 合法范围内。

- event_loops 结构与 admin_loop 规则
  - 渲染后的 JSON 必须是 object，并且包含 `event_loops` 列表。
  - 必须存在 `name == "admin_loop"` 的 loop，并且 `busy_spin` 必须为 `false`。
  - 任意 `busy_spin == true` 的 loop，其 `cpu_id` 必须属于 `isolated_cpus`。

- host-level log_dir 注入
  - 若 `deployments/<dc>/deployments.yaml` 的 host 下定义了 `log_dir`：
    - 则每个生成的配置必须包含 `logging` object；
    - 工具会强制写入 `logging.log_dir = <log_dir>/<application_name>`。

- 共享内存（shm）同机约束
  - 任意跨应用引用 `{{OtherApp.key}}`，若 `key` 名包含 `shm`（不区分大小写），视为共享内存相关。
  - 此时要求引用方与被引用方必须在同一 `(dc, host)` 上，否则 `make config` 报错。

- 模板变量必须可解析
  - 渲染完成后若仍残留 `{{...}}`（例如模板变量拼写错误、cfg_envs 缺失），会直接报错。
  - 跨应用引用时：
    - 被引用的 application 必须存在；
    - key 必须在被引用 application 的 `cfg_envs` 中存在。
  - 若引用 `listen_nic`：
    - 必须能在被引用 application 所在 host 的 `hosts.yaml` 中找到对应网卡名并解析到 IP，否则报错。

---

## 后续可扩展方向

- 支持更多应用类型和更复杂的模板占位符；
- 增强跨应用依赖描述（不仅 recorder 连接 publisher）；
- 与远端部署系统对接（目前仅支持本地生成，不涉及远程分发）。
