# 设计一个部署工具
我们今天一起来设计一个可以将binary，configure文件部署到远程服务器的工具，我的大致想法是这样的：

## 二进制binary
1. binary我们可以发布在github上，通过gitlfs下载到本地 - binary我指的是某个编译好的已经发布的二进制应用程序比如：md_server
2. 一个binary可以有多个版本的tag, 例如v1.0.0, v1.0.1, v1.0.2等
3. 我们对每个binary的版本可以设定一个tag，比如：prod，staging，latest 他们分别代表不同环境的版本号

## 应用程序
1. 应用程序指的是某个application，各自有一个名字，比如：dce_md_publisher, dce_md_recorder
2. 每个application指向一个binary + tag，比如：dce_md_publisher指向md_server + prod
3. 每个application可以有一个或者多个配置文件，比如：dce_md_publisher可以通过dce_md_publisher.json来配置 （默认使用于AppName一致的名字）
4. 每个application默认会直接使用./applicationName来运行，默认会使用当前目录下与applicationName一致的配置文件

## 配置文件
1. 每个应用程序的配置文件可以在repo中定义一个template
2. 其中有一些变量，例如：CPU id，某些shm的名字，host/port之类的需要在部署的时候替换

## 定时
1. 我们需要有一个定时配置文件，用来定义哪些application需要定时启动/停止
2. 定时配置文件可以定义多个定时任务，每个定时任务可以定义开始时间、结束时间、重复周期等

## 机房/机器
1. 我们可以将目录按不同的机房划分
2. 每个机房下面可以有一个配置文件列举所有机器
3. 每个机器我们需要有一个简单的CPU配置的描述comments
4. 每个applicaition部署在哪几个核上，使用哪个网卡interface需要定义清楚

## 用法简记
作为developer工作流程是：
1. clone 这个repo, pull `release` branch
2. 在本地对应的机房目录下部署需要的applicaitions，定义他们的configure
3. 通过make或者其他脚本，生成对应的部署文件夹
4. 每个文件夹内有applicationName link to binary/tag
5. 文件夹内有生成的configure文件，相关的env已经被替换成了明文
6. 在各自目录下，如果使用application框架，可以使用 ./applicationName -v来validate配置文件

综上，我们希望设计一个部署工具，能够帮助我们快速部署和管理这些应用程序，我们可以一步步来细化需求。