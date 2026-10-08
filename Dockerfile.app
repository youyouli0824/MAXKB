# =============================================================================
# 将本项目（MaxKB）整体打包为一个自包含的 Docker 镜像，用于云服务器部署。
#
# 一、构建思路
#   官方 1panel/maxkb 是「一体化」镜像：容器内自带 PostgreSQL(pgvector)、Redis、
#   本地 embedding 模型以及全部 Python 依赖，一条 docker run 就能跑起来。
#   本文件在此基础上，把本仓库的全量后端源码覆盖进去，得到
#   「官方运行时 + 本项目代码」的完整镜像。
#
# 二、为什么可以只覆盖源码、不重装依赖
#   本仓库 pyproject.toml 与镜像对应的 v2.10.6-lts 标签逐行一致（已核对），
#   即 Python 依赖集合没有任何变化，因此直接覆盖 apps/ 即可，不存在依赖错配，
#   也就无需重新执行 uv/pip 安装（可省去数 GB 下载和数十分钟构建）。
#
# 三、沿用镜像自带、不会被覆盖的部分
#   - /opt/maxkb-app/ui/dist            前端构建产物（本次改动不涉及前端逻辑）
#   - apps/locales/*/LC_MESSAGES/*.mo    已编译语言包（本地只有 .po，未编译）
#   - /opt/maxkb-app/model               本地 embedding 模型
#   - /opt/py3、site-packages            已安装的 Python 依赖
#   COPY 是「合并」语义：源里没有的文件不会删除，所以上述内容都会保留。
#
# 四、已知差异（有意为之）
#   installer/sandbox.c 相比 v2.10.6-lts 多了 2 行改动（禁止 dlopen 加载 _cffi），
#   需要 gcc 重新编译 sandbox.so，而官方运行时镜像内不含 gcc。
#   该改动是沙箱的加固项，与本次 PDF 分段/OCR 功能无关，故本镜像沿用官方
#   已编译的 sandbox.so。如确需生效，可改用 installer/Dockerfile 做全量源码构建。
#
# 五、构建与导出
#   docker build -f Dockerfile.app -t maxkb-app:v2.10.6-lts .
#   docker save maxkb-app:v2.10.6-lts | gzip > maxkb-app-v2.10.6-lts.tar.gz
#
# 六、服务器上运行
#   gunzip -c maxkb-app-v2.10.6-lts.tar.gz | docker load
#   docker run -d --name maxkb --restart always -p 8080:8080 \
#     -v /opt/maxkb:/opt/maxkb \
#     -e MAXKB_DEFAULT_PASSWORD='换成你的强密码' \
#     maxkb-app:v2.10.6-lts
#   说明：/opt/maxkb 是数据目录（数据库、上传文件、日志），必须挂到宿主机做持久化。
# =============================================================================

FROM 1panel/maxkb:v2.10.6-lts

# ---- 1) 覆盖后端运行时代码 --------------------------------------------------
# apps/ 下包含本次 PDF 分段与扫描件 OCR 改动、新增模块
# （common/mcp、common/signing、oss/url_fetch、handle/impl/text/pdf_ocr_helper 等），
# 以及数据库迁移脚本 —— 容器启动时由 main.py 自动执行 migrate。
COPY apps/ /opt/maxkb-app/apps/

# ---- 2) 覆盖入口脚本与依赖声明 ----------------------------------------------
# main.py 是唯一入口；pyproject.toml 与镜像依赖一致，放进来便于排查与版本对照。
COPY main.py /opt/maxkb-app/main.py
COPY pyproject.toml /opt/maxkb-app/pyproject.toml

# ---- 3) 清理本地带进来的字节码缓存 ------------------------------------------
# 本地开发环境是 CPython 3.13，镜像内是 3.11；.pyc 无意义且会干扰排错，统一清掉，
# 运行时 Python 会按需重新生成。
RUN find /opt/maxkb-app/apps -name '__pycache__' -type d -prune -exec rm -rf {} + || true

# ---- 4) 本次改动涉及功能的默认开关 ------------------------------------------
# 仍可在 docker run / compose 中用同名变量覆盖。
#   MAXKB_PDF_HEADING_PATTERN_ENABLE: 中文公文标题与正文同字号时按文本特征补标题标记
#   MAXKB_PDF_OCR_*                 : 扫描版 PDF 走视觉模型 OCR 的兜底开关与限额
ENV MAXKB_PDF_HEADING_PATTERN_ENABLE=true \
    MAXKB_PDF_OCR_ENABLE=true \
    MAXKB_PDF_OCR_MAX_PAGES=100 \
    MAXKB_PDF_OCR_IMAGE_MAX_SIDE=2000

WORKDIR /opt/maxkb-app
EXPOSE 8080
VOLUME /opt/maxkb
ENTRYPOINT ["bash", "-c"]
CMD ["/usr/bin/start-all.sh"]
