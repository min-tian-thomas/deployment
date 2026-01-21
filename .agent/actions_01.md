# 第一阶段：最小可运行闭环（目标：1个应用、1台机器、生成1个目录）
## 步骤 1.0
创建最简目录结构（只包含今天要用到的部分）

```
deployment/
├── binaries/
│   └── md_server.yaml
├── apps/
│   └── dce_md_publisher.yaml
├── datacenters/
│   └── datacenters.yaml
└── deploy/
    └── idc_shanghai/
        ├── deployments.yaml
        └── templates/
            └── dce_md_publisher.json
```

今天只创建这 5 个文件，其他目录和文件先不要碰。
## 步骤 1.1
填充最简内容（复制下面内容即可，后面再改）
binaries/md_server.yaml

``` 
name: md_server
tags:
  prod: v1.2.3
  staging: v1.2.4-rc1
  latest: v1.2.4
```
apps/dce_md_publisher.yaml

```
name: dce_md_publisher
binary: md_server
default_tag: prod

config:
  template_name: dce_md_publisher.json
```

datacenters/datacenters.yaml

```
datacenters:
  - id: idc_shanghai
    hosts:
      - name: host01
        cpus: 16
        numa_nodes: 2
        log_cpus: 0,1
        isolated_cpus: 2-15
        nics:
          - name: eth0
            type: ethernet
          - name: sf0
            type: solarflare
```

deploy/idc_shanghai/deployments.yaml

```
deployments:
  host01:
    - app: dce_md_publisher
      isolated_cpus: 2
      shared_cpus: 0, 1  # for logging and admin
      cfg_envs:
        - 
          listen_nic: eth0
          listen_port: 8080
          log_cpu: 0
          main_loop_cpu: 2
          admin_loop_cpu: 1

```

deploy/idc_shanghai/templates/dce_md_publisher.json

```
{
    "logging": {
        "log_level": "Info",
        "log_cpu": {{log_cpu}}
    },
    "event_loops": [
        {
            "name": "main_loop",
            "cpu_id": {{main_loop_cpu}},
            "busy_spin": true
        },
        {
            "name": "admin_loop",
            "cpu_id": {{admin_loop_cpu}},
            "busy_spin": false
        }
    ],
    "listen_nic": "{{listen_nic}}",
    "listen_port": {{listen_port}}
}
```

我们可以生成一个make的脚本，或者其他脚本也可以，来生成这个配置文件，同时检查：
- 配置文件中的busy spin的cpu_id是否在isolated_cpus范围内
- 为配置文件的每个CPU id分配一个comments，说明这个cpu所在的numa节点
- 配置文件中的log_cpu是否在shared_cpus范围内
- 配置文件中的cpu_id是否重复



