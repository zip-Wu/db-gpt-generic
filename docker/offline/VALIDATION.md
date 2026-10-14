# 离线交付验证记录

构建日期：2026-09-09。平台基线：feature/k-ics-rebrand，1b4325a3，
另含本次现场适配修改，随包提供 source.tar.gz。

## 交付内容

- k-ics:20260909：平台、已构建前端、Python 3.11、UV 管理的运行依赖、
  Oracle Instant Client 19.32 Basic、离线分词缓存。
- k-ics-python:20260909：Python 分析沙箱，包括 pandas、Excel、图表、
  Word/PPT/PDF 常用分析库及中文字体。
- site.toml、site.env.example、start.sh、README.md：现场配置和启动说明。
- images-manifest.json、SHA256SUMS：实际镜像信息及文件校验值。

基础镜像使用已下载的 Python 3.11 slim，实际系统为 Debian 13，架构为
linux/amd64。构建使用清华 Debian/PyPI 源；现场不需要 apt、pip 或 UV 下载。
本次复用仓库内已有前端静态产物，因此未使用 Node 和 Ubuntu runner 镜像。

## 已验证

- 本地隔离 Python 环境：63 条针对性回归测试通过，涵盖 SM3 鉴权、
  请求签名更新、普通/流式响应和工具调用、HTTP 200 业务错误、Oracle
  服务名连接串、Thick 初始化、沙箱隔离和清理、文件分析及技能上传校验。
- 构建服务器：Linux 6.12 / x86_64、Docker 29.1.3。
- 应用容器使用 `--network none`：健康接口正常，首页 HTTP 200。
- Oracle Thick 客户端成功加载，版本 19.32.0.0.0。
- cl100k_base、o200k_base 分词缓存在断网时可用。
- 独立 Python 沙箱生成 Excel 和 PNG，无法访问外网、宿主 Docker socket
  或平台凭据；超时后容器被清理。
- 技能脚本使用独立沙箱，保留输入文件挂载、结构化输出及图片返回。

## 现场验收项

本次未使用真实 appKey/appSecret 或数据库密码，未完成客户网关、Embedding
和 Oracle 的真实业务联调。交付包不包含这些凭据或客户 CA 证书。

1. 确认麒麟现场 Docker 版本及磁盘空间，加载镜像并启动；目前未在客户的
   4.19 内核上实际运行，不能用构建服务器结果替代现场验收。
2. 填写 site.env 的 appKey/appSecret，配置网关 CA，校准服务器时间。
3. 确认应用容器可访问网关和 Embedding 内网地址；Embedding 不是本包内的服务。
4. Oracle 按现场实际填写 host/port/service_name 与数据库账号，
   验证连接、表结构读取、中文字段与查询结果。
5. 验证普通/流式对话、工具调用、数据库问答及上传文件分析。

平台元数据库使用 SQLite，向量存储使用本地 Chroma；无需另备 MySQL、
PostgreSQL 或向量数据库镜像。LLM、Embedding 和业务 Oracle 均由现场提供。
宿主机需要预装 Docker Engine。Docker 安装程序不包含在本包中。

Python/Shell 分析与技能脚本已接入沙箱。旧 Lyric 脚本/AWEL Lyric 路径在
Docker 模式下拒绝执行，Notebook、浏览器自动化和 GPU 执行不在本包范围内。
应用通过宿主 Docker socket 管理沙箱，应部署在受信任主机并限制管理入口。
