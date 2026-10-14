import logging
import os
import sys
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# fastapi import time cost about 0.05s
from fastapi.staticfiles import StaticFiles

from starlette.middleware.gzip import GZipMiddleware
from dbgpt._version import version
from dbgpt.component import SystemApp
from dbgpt.configs.model_config import (
    LOGDIR,
    STATIC_MESSAGE_IMG_PATH,
)
from dbgpt.util.fastapi import build_cors_config, create_app, replace_router
from dbgpt.util.i18n_utils import _, set_default_language
from dbgpt.util.parameter_utils import _get_dict_from_obj
from dbgpt.util.system_utils import get_system_info
from dbgpt.util.tracer import SpanType, SpanTypeRunName, initialize_tracer, root_tracer
from dbgpt.util.utils import (
    logging_str_to_uvicorn_level,
    setup_http_service_logging,
    setup_logging,
)
from dbgpt_app.base import (
    _create_model_start_listener,
    _migration_db_storage,
    server_init,
)

# initialize_components import time cost about 0.1s
from dbgpt_app.component_configs import initialize_components
from dbgpt_app.config import ApplicationConfig, ServiceWebParameters, SystemParameters
from dbgpt_serve.core import add_exception_handler


class _SelectiveGZipMiddleware(GZipMiddleware):
    """GZip 中间件——静态资源 + 大数据 JSON 接口压缩，SSE 流式直通。

    现场适配修正（2026-09-14 + 09-16）：
    原版直接 `app.add_middleware(GZipMiddleware)` 是全局的，会对所有响应
    启用 gzip。Starlette 的 GZipResponder 对流式响应（SSE，more_body=True）
    逐块 `gzip_file.write()` 但**不 flush**，而 gzip.GzipFile 内部有压缩
    缓冲，写入的字节不会立即落到响应流，只有流结束 close() 时才一次性
    释放——导致 ReAct 的 step.start/step.chunk/step.done 等 SSE 事件被
    积压在 gzip 缓冲里，前端全程看不到实时流程，直到整个问答结束才
    一次性收到全部内容（表现为"一直在思考/转圈，结束才出结果"）。

    修复：仅对静态资源路径（大 JS/图片）和确认非流式的 JSON 接口
    （如 observability trace 全量响应，单条可达数 MB）启用 gzip，
    /api 其余路径（含 SSE 对话流）直通。
    """

    _COMPRESS_PREFIXES = ("/_next/static", "/images", "/swagger_static", "/api/v1/observability")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "") or ""
        if path.startswith(self._COMPRESS_PREFIXES):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)


logger = logging.getLogger(__name__)
ROOT_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT_PATH)

app = create_app(
    title=_("DB-GPT Open API"),
    description=_("DB-GPT Open API"),
    version=version,
    openapi_tags=[],
)
# Use custom router to support priority
replace_router(app)

system_app = SystemApp(app)


def mount_routers(app: FastAPI):
    """Lazy import to avoid high time cost"""
    from dbgpt_app.knowledge.api import router as knowledge_router
    from dbgpt_app.openapi.api_v1.agentic_data_api import router as agentic_data_api
    from dbgpt_app.openapi.api_v1.api_v1 import router as api_v1
    from dbgpt_app.openapi.api_v1.editor.api_editor_v1 import (
        router as api_editor_route_v1,
    )
    from dbgpt_app.openapi.api_v1.examples_api import router as examples_router
    from dbgpt_app.openapi.api_v1.feedback.api_fb_v1 import router as api_fb_v1
    from dbgpt_app.openapi.api_v1.python_upload_api import (
        router as python_upload_router,
    )
    from dbgpt_app.openapi.api_v2 import router as api_v2
    from dbgpt_serve.agent.app.controller import router as gpts_v1
    from dbgpt_serve.agent.app.endpoints import router as app_v2

    app.include_router(api_v1, prefix="/api", tags=["Chat"])
    app.include_router(api_v2, prefix="/api", tags=["ChatV2"])
    app.include_router(api_editor_route_v1, prefix="/api", tags=["Editor"])
    app.include_router(api_fb_v1, prefix="/api", tags=["FeedBack"])
    app.include_router(gpts_v1, prefix="/api", tags=["GptsApp"])
    app.include_router(app_v2, prefix="/api", tags=["App"])
    app.include_router(python_upload_router, prefix="/api", tags=["PythonUpload"])
    app.include_router(examples_router, prefix="/api", tags=["Examples"])
    app.include_router(agentic_data_api, prefix="/api", tags=["AgenticData"])

    app.include_router(knowledge_router, prefix="/api/v1", tags=["Knowledge"])

    from dbgpt_serve.agent.app.recommend_question.controller import (
        router as recommend_question_v1,
    )

    app.include_router(recommend_question_v1, prefix="/api", tags=["RecommendQuestion"])


def _immutable_static_files(directory: str) -> StaticFiles:
    """StaticFiles that never re-fetches content-hashed assets.

    现场适配：/_next/static 下的产物文件名都带内容 hash（内容不变名不变），
    可安全长缓存，避免每次打开页面都重新下载十几 MB 解析。
    """

    class _ImmutableStaticFiles(StaticFiles):
        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    return _ImmutableStaticFiles(directory=directory)


def _revalidating_static_files(directory: str) -> StaticFiles:
    """StaticFiles that always revalidate, so HTML can never be served stale.

    现场适配：这个挂载服务的入口资源（各页面的 index.html）文件名固定、内容随
    每次构建变化。Starlette 默认只发 ETag/Last-Modified、不发 Cache-Control，
    浏览器因此可以对它启用启发式缓存——在"新鲜期"内直接复用本地副本，连一次
    校验都不发。而它引用的 /_next/static 产物是 immutable 长缓存的，旧 chunk
    仍留在客户端缓存里，于是浏览器会整包跑上一次构建的代码：页面看起来完全
    没更新，直到用户手动强制刷新。显式声明 no-cache 后每次都会带 ETag 校验，
    内容没变仍是 304（一个来回、无正文），改了就立刻拿到新 HTML。

    只对本挂载生效；/_next/static 走单独的 immutable 挂载，不受影响。
    """

    class _RevalidatingStaticFiles(StaticFiles):
        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

        async def get_response(self, path: str, scope):
            # 304（未修改）走的是 NotModifiedResponse，不经过 file_response，
            # 这里补同一份声明，保证缓存语义在命中校验时也保持一致。
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

    return _RevalidatingStaticFiles(directory=directory, html=True)


def mount_static_files(app: FastAPI, param: ApplicationConfig):
    package_dir = os.path.dirname(os.path.abspath(__file__))
    if param.service.web.new_web_ui:
        static_file_path = os.path.join(package_dir, "static", "web")
    else:
        static_file_path = os.path.join(package_dir, "static", "old_web")

    os.makedirs(STATIC_MESSAGE_IMG_PATH, exist_ok=True)

    # 现场适配：对静态资源启用 gzip 压缩（原版未启用，整包明文传输大 JS 极慢）。
    # 注意：不能用全局 GZipMiddleware——它会缓冲 SSE 流式响应（ReAct 步骤事件
    # 被积压在 gzip 缓冲里，前端全程看不到实时流程）。只压缩静态资源路径。
    app.add_middleware(_SelectiveGZipMiddleware, minimum_size=1024)

    app.mount(
        "/images",
        StaticFiles(directory=STATIC_MESSAGE_IMG_PATH, html=True),
        name="static2",
    )
    app.mount(
        "/_next/static",
        _immutable_static_files(static_file_path + "/_next/static"),
    )

    # Serve the Next.js dynamic route page for /share/{token}.
    # Next.js static export produces share/[token]/index.html (literal directory
    # name "[token]"), but FastAPI StaticFiles cannot resolve dynamic segments.
    # Register explicit routes *before* the catch-all StaticFiles mount so that
    # /share/<any-token> is served correctly.
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    share_html = os.path.join(static_file_path, "share", "[token]", "index.html")

    @app.get("/share/{token}")
    @app.get("/share/{token}/")
    async def _share_page_fallback(token: str):
        if os.path.isfile(share_html):
            return FileResponse(
                share_html,
                media_type="text/html",
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )
        raise HTTPException(status_code=404, detail="Page not found")

    app.mount("/", _revalidating_static_files(static_file_path), name="static")

    app.mount(
        "/swagger_static",
        StaticFiles(directory=static_file_path),
        name="swagger_static",
    )


add_exception_handler(app)


def initialize_app(param: ApplicationConfig, args: List[str] = None):
    """Initialize app
    If you use gunicorn as a process manager, initialize_app can be invoke in
    `on_starting` hook.
    Args:
        param:WebServerParameters
        args:List[str]
    """

    # import after param is initialized, accelerate --help speed
    from dbgpt.model.cluster import initialize_worker_manager_in_client

    web_config = param.service.web
    log_config = web_config.log or param.log
    setup_logging(
        "dbgpt",
        log_config,
        default_logger_filename=os.path.join(LOGDIR, "dbgpt_webserver.log"),
    )

    server_init(param, system_app)
    mount_routers(app)
    model_start_listener = _create_model_start_listener(system_app)
    initialize_components(
        param,
        system_app,
    )
    system_app.on_init()

    # Migration db storage, so you db models must be imported before this
    _migration_db_storage(
        param.service.web.database, web_config.disable_alembic_upgrade
    )

    # After init, when the database is ready
    system_app.after_init()

    # Register default data sources
    # 部署可选项：不使用内置示例库（Walmart_Sales）时通过环境变量关闭，
    # 自动注册：否则每次重建容器，只要元数据库里没有该记录，就会重新注入，
    # 表现为"删了沃尔玛，重启又冒出来"。通过环境变量 DBGPT_DISABLE_DEFAULT_DATA_SOURCES
    # 控制（现场 site.env 置 true），默认保持原逻辑。
    if (
        os.getenv("DBGPT_DISABLE_DEFAULT_DATA_SOURCES", "false").lower() == "true"
    ):
        logger.info("Default data sources registration disabled (DBGPT_DISABLE_DEFAULT_DATA_SOURCES=true)")
    else:
        try:
            from dbgpt.configs.model_config import PILOT_PATH, ROOT_PATH
            from dbgpt_serve.datasource.manages.connect_config_db import ConnectConfigDao

            dao = ConnectConfigDao()
            db_name = "Walmart_Sales"
            if not dao.get_by_names(db_name):
                candidate_paths = [
                    os.path.join(PILOT_PATH, "examples", "Walmart_Sales.db"),
                    os.path.join(
                        ROOT_PATH, "docker", "examples", "dashboard", "Walmart_Sales.db"
                    ),
                ]
                db_absolute_path = next(
                    (p for p in candidate_paths if os.path.isfile(p)), None
                )
                if db_absolute_path is None:
                    logger.info(
                        f"Skipping default data source '%s': file not found in any "
                        f"{db_name} at {candidate_paths}"
                    )
                else:
                    dao.add_file_db(
                        db_name=db_name,
                        db_type="sqlite",
                        db_path=db_absolute_path,
                        comment="Default Walmart Sales example database",
                    )
                    logger.info(
                        f"Successfully registered default data source: "
                        f"{db_name} at {db_absolute_path}"
                    )
        except Exception as e:
            logger.error(f"Failed to register default data sources: {str(e)}")

    binding_port = web_config.port
    binding_host = web_config.host
    if not web_config.light:
        from dbgpt.model.cluster.storage import ModelStorage
        from dbgpt_serve.model.serve import Serve as ModelServe

        logger.info(
            "Model Unified Deployment Mode, run all services in the same process"
        )
        model_serve = ModelServe.get_instance(system_app)
        # Persistent model storage
        model_storage = ModelStorage(model_serve.model_storage)
        initialize_worker_manager_in_client(
            worker_params=param.service.model.worker,
            models_config=param.models,
            app=app,
            binding_port=binding_port,
            binding_host=binding_host,
            start_listener=model_start_listener,
            system_app=system_app,
            model_storage=model_storage,
        )

    else:
        # MODEL_SERVER is controller address now
        controller_addr = web_config.controller_addr
        param.models.llms = []
        param.models.rerankers = []
        param.models.embeddings = []
        initialize_worker_manager_in_client(
            worker_params=param.service.model.worker,
            models_config=param.models,
            app=app,
            run_locally=False,
            controller_addr=controller_addr,
            binding_port=binding_port,
            binding_host=binding_host,
            start_listener=model_start_listener,
            system_app=system_app,
        )

    mount_static_files(app, param)

    # Before start, after on_init
    system_app.before_start()
    return param


def run_uvicorn(param: ServiceWebParameters):
    import uvicorn

    setup_http_service_logging()

    # https://github.com/encode/starlette/issues/617
    cors_app = CORSMiddleware(
        app=app,
        **build_cors_config(param.cors_allowed_origins),
    )
    log_level = "info"
    if param.log:
        log_level = logging_str_to_uvicorn_level(param.log.level)
    uvicorn.run(
        cors_app,
        host=param.host,
        port=param.port,
        log_level=log_level,
    )


def run_webserver(config_file: str):
    # Load configuration with specified config file
    param = load_config(config_file)
    trace_config = param.service.web.trace or param.trace
    trace_file = trace_config.file or os.path.join(
        "logs", "dbgpt_webserver_tracer.jsonl"
    )
    config = system_app.config
    config.configs["app_config"] = param
    initialize_tracer(
        trace_file,
        system_app=system_app,
        root_operation_name=trace_config.root_operation_name or "DB-GPT-Webserver",
        tracer_parameters=trace_config,
    )

    with root_tracer.start_span(
        "run_webserver",
        span_type=SpanType.RUN,
        metadata={
            "run_service": SpanTypeRunName.WEBSERVER,
            "params": _get_dict_from_obj(param),
            "sys_infos": _get_dict_from_obj(get_system_info()),
        },
    ):
        param = initialize_app(param)

        # TODO
        from dbgpt_serve.agent.agents.expand.app_start_assisant_agent import (  # noqa: F401
            StartAppAssistantAgent,
        )
        from dbgpt_serve.agent.agents.expand.intent_recognition_agent import (  # noqa: F401
            IntentRecognitionAgent,
        )

        run_uvicorn(param.service.web)


def scan_configs():
    from dbgpt.model import scan_model_providers
    from dbgpt_app.initialization.app_initialization import scan_app_configs
    from dbgpt_app.initialization.serve_initialization import scan_serve_configs
    from dbgpt_ext.storage import scan_storage_configs
    from dbgpt_serve.datasource.manages.connector_manager import ConnectorManager

    cm = ConnectorManager(system_app)
    # pre import all connectors
    cm.on_init()
    # Register all model providers
    scan_model_providers()
    # Register all serve configs
    scan_serve_configs()
    # Register all storage configs
    scan_storage_configs()
    # Register all app configs
    scan_app_configs()


def load_config(config_file: str = None) -> ApplicationConfig:
    from dbgpt._private.config import Config
    from dbgpt.configs.model_config import ROOT_PATH as DBGPT_ROOT_PATH

    if config_file is None:
        config_file = os.path.join(
            DBGPT_ROOT_PATH, "configs", "dbgpt-proxy-siliconflow.toml"
        )
    elif not os.path.isabs(config_file):
        # If config_file is a relative path, make it relative to DBGPT_ROOT_PATH
        config_file = os.path.join(DBGPT_ROOT_PATH, config_file)

    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file not found: {config_file}")
    from dbgpt.util.configure import ConfigurationManager

    logger.info(f"Loading configuration from: {config_file}")
    cfg = ConfigurationManager.from_file(config_file)
    sys_config = cfg.parse_config(SystemParameters, prefix="system")
    # Must set default language before any i18n usage
    set_default_language(sys_config.language)
    _CFG = Config()
    _CFG.LANGUAGE = sys_config.language

    # Scan all configs
    scan_configs()

    app_config = cfg.parse_config(ApplicationConfig, hook_section="hooks")
    return app_config


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description="DB-GPT Webserver")
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Path to the configuration file. Default: configs/dbgpt-siliconflow.toml",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Parse command line arguments
    _args = parse_args()
    _config_file = _args.config
    run_webserver(_config_file)
