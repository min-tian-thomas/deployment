# 部署工具设计（阶段一草案）

## 阶段一目标

设计并固化一套 **只负责本地生成部署目录与配置** 的工具和配置约定：

- 输入：
  - binary 版本与 tag 映射
  - application 抽象定义
  - 机房与机器的静态信息（CPU/网卡等）
  - 按机房/机器视角声明的部署计划（每台机器跑哪些 app、用哪些核/端口/网卡）
  - 每个机房内的时间调度规则
  - 各机房内的应用配置模板
- 输出：
  - 每个机房下，按 `host/app` 组织的 **部署目录**
    - 包含指向具体 binary 版本的 link/启动脚本
    - 包含渲染完成的配置文件（模板变量已替换为明文）
- 不负责：
  - 远程同步（rsync/scp）、远程执行命令
  - 密钥/密码等敏感配置的管理（由现有方案解决）

所有源配置和生成物统一放在 git 仓库中，通过 branch/tag 管理快照和回滚，不再单独设计 snapshot 文件。

---

## 顶层目录结构（阶段一）

以 `~/projects/workspace/ninth/deployment/` 为根目录，整体形状如下：

```text
deployment/
  README.md

  binaries/                      # 各个 binary 的版本 & tag 映射（源配置）
    md_server.yaml
    another_binary.yaml

  apps/                          # 应用抽象：binary + 默认 tag + 启动/停止方式（源配置）
    dce_md_publisher.yaml
    dce_md_recorder.yaml

  datacenters/                   # 机房/机器统一描述（源配置）
    datacenters.yaml             # 所有机房 & host CPU/网卡信息

  deploy/                        # 按机房视角的部署定义 + 生成物
    idc_shanghai/
      deployments.yaml           # 源配置：每台机器、每个 app、用哪些核/网卡/端口
      schedules.yaml             # 源配置：该 DC 内的时间窗口/周期

      templates/                 # 源配置：只给上海机房用的 app 配置模板
        dce_md_publisher.json
        dce_md_recorder.json

      applications/              # 生成物：上海 DC 的实际应用目录
        host01/
          dce_md_publisher/
            dce_md_publisher     # link 或 wrapper → 对应 binary 具体版本
            dce_md_publisher.json
          dce_md_recorder/
            dce_md_recorder
            dce_md_recorder.json
        host02/
          ...

    idc_beijing/
      deployments.yaml
      schedules.yaml
      templates/                 # 源配置：北京机房自己的模板
        dce_md_publisher.json
      applications/              # 生成物：北京 DC 的实际应用目录
        host11/
          ...

  tools/                         # 部署工具实现（后续阶段设计）
    # cli.py / main.cpp / ...
```

说明：

- `binaries/`、`apps/`、`datacenters/`、`deploy/*/deployments.yaml`、`deploy/*/schedules.yaml`、`deploy/*/templates/` 都是 **源配置**，由人编辑 & code review。
- `deploy/*/applications/` 是工具根据源配置 **生成的部署目录**，同样纳入 git 管理，但在工作流上约定为“自动生成，不手改”。

---

## 核心概念模型（阶段一）

### 1. Binary（可执行程序本体）

- 存放在 `binaries/*.yaml` 中，描述：
  - binary 名称（例如 `md_server`）
  - 已发布的版本列表及 checksum
  - tag → version 映射（例如 `prod`、`staging`、`latest`）
  - 获取 artifact 的方式（例如 GitHub release、内部制品库等）
- 工具职责：
  - 根据 binary 名称 + tag 解析出具体 version
  - 按约定规则在本地下载/更新对应 binary

### 2. Application（应用抽象）

- 存放在 `apps/*.yaml` 中，描述：
  - 应用名称（例如 `dce_md_publisher`）
  - 绑定的 binary 名称（例如 `md_server`）
  - 默认使用的 binary tag（例如 `prod`）
  - 默认启动命令、停止命令
  -（可选）校验命令，例如 `./app -v -c config.json`
- 注意：
  - 大部分 app 是统一 application 框架，支持 `-v` 校验配置
  - 留口子支持少数非框架应用，通过自定义启动/校验命令描述

### 3. Datacenter / Host（机房 & 机器）

- 集中定义在 `datacenters/datacenters.yaml`：
  - `datacenter`：机房标识（例如 `idc_shanghai`、`idc_beijing`）
  - `hosts`：每个机房下的主机列表
    - IP、hostname、标签（机架、环境等）
    - CPU 拓扑信息（总核数、NUMA 节点、推荐实时核等）
    - 网卡列表及角色（内部/外部等）
- 工具职责：
  - 校验 `deploy/*/deployments.yaml` 中声明的 CPU 核/网卡是否存在、是否越界

### 4. Deployments（按机房/机器视角的部署计划）

- 每个机房一个文件：`deploy/<dc>/deployments.yaml`，例如 `deploy/idc_shanghai/deployments.yaml`。
- 按“机器视角”描述：
  - 这台机器上跑哪些 app
  - 每个 app 实例绑定哪些 CPU、网卡、端口等
- 示例（仅结构）：

```yaml
datacenter: idc_shanghai

deployments:
  host01:
    - app: dce_md_publisher
      instance: md_pub_1
      binary_tag: prod        # 可选：覆盖 app 默认 tag
      cpu_set: "2-3"
      nic: "eth0"
      listen_port: 9001

    - app: dce_md_recorder
      instance: md_rec_1
      cpu_set: "4-5"
      nic: "eth0"
      listen_port: 9101

  host02:
    # ...
```

- 工具职责：
  - 将 deployments 中的每条记录映射为 `applications/<host>/<app>/` 下的具体目录与配置
  - 保证字段完整性和约束检查（例如端口不冲突、CPU 核未越界等 —— 细节可在后续阶段增加）

### 5. Schedules（时间调度规则）

- 每个机房一个文件：`deploy/<dc>/schedules.yaml`。
- 描述该机房内通用或特定的时间窗口/周期，例如交易时间：

```yaml
datacenter: idc_shanghai

schedules:
  trading-session-daytime:
    description: "日盘交易时间"
    timezone: "Asia/Shanghai"
    rules:
      - start: "09:20"
        end: "11:30"
        days: "mon-fri"
      - start: "13:00"
        end: "15:30"
        days: "mon-fri"
```

- 在 `deployments.yaml` 中，app 实例可以引用这些 schedule：

```yaml
schedules:
  - trading-session-daytime
```

- 工具职责：
  - 将 schedule 信息一并写入生成的目录/配置（例如生成 cron 片段或供上层系统使用的描述），阶段一可以只保证结构正确，具体执行方式后续再定。

### 6. Templates（配置模板，按机房划分）

- 模板不再放在全局 `templates/`，而是 **下沉到各个 data center**：
  - `deploy/idc_shanghai/templates/*.json`
  - `deploy/idc_beijing/templates/*.json`
- `apps/*.yaml` 中只定义模板名称，不写具体路径，例如：

```yaml
config:
  template_name: dce_md_publisher.json
```

- 实际渲染时，工具按 data center 约定查找：
  - `deploy/<dc>/templates/<template_name>`
- 好处：
  - 应用抽象定义与 data center 解耦
  - 各 DC 可以使用同名但内容不同的模板做本地化（如 CPU 布局、默认 shm 命名规范等）

### 7. Applications（生成的部署目录）

- 工具根据上述所有源配置，生成：
  - `deploy/<dc>/applications/<host>/<app>/` 结构
  - 目录内：
    - `applicationName`：
      - 指向实际 binary 的 symlink，或一层 wrapper 脚本
    - 渲染后的配置文件，例如 `applicationName.json`
- 约定：
  - `applications/` 目录 **不手工修改**，全部由工具生成
  - 但依然纳入 git 管理，方便 review 和回滚

---

## 阶段一不做的事情

- 不实现：
  - rsync/scp 到远程机器
  - ssh 登录远程机器执行启动/停止命令
  - crontab/systemd 等实际安装（可以仅生成对应的配置片段）
- 不引入单独的 snapshot 目录：
  - 通过 git 的 commit 与 tag 来管理部署快照

---

## 下一阶段可能扩展点（仅列出，不在本阶段实现）

- 为部署工具设计具体 CLI：如 `deploy plan` / `deploy build` / `deploy validate` 等
- 增加更多一致性检查：
  - 端口冲突检测（同机房/同机器）
  - CPU 亲和设置与 NUMA 拓扑的高级约束
- 生成并协同管理：
  - crontab 片段、systemd service/timer 文件
- 与现有敏感配置管理方案的集成接口（注入密钥/证书等）
