"""DBSummaryClient class."""

import logging
import threading
import time
import traceback
from typing import Any, Dict, Tuple

from dbgpt.component import SystemApp
from dbgpt.core import Embeddings
from dbgpt.rag.embedding.embedding_factory import EmbeddingFactory
from dbgpt.rag.text_splitter.text_splitter import RDBTextSplitter
from dbgpt.storage.vector_store.base import VectorStoreBase
from dbgpt_ext.rag import ChunkParameters
from dbgpt_ext.rag.summary.gdbms_db_summary import GdbmsSummary
from dbgpt_ext.rag.summary.rdbms_db_summary import RdbmsSummary
from dbgpt_serve.datasource.manages import ConnectorManager
from dbgpt_serve.rag.storage_manager import StorageManager

logger = logging.getLogger(__name__)


# ── 索引构建运行状态（进程内，供「索引维护」状态端点查询）───────────────
# {dbname: {"state": "building"|"idle", "started_at": ts, "finished_at": ts,
#           "last_success_at": ts, "last_error": str|None}}
# 只记录状态、不参与构建逻辑；进程重启后回到初始值（首次构建成功时补记）。
_INDEX_STATE: Dict[str, Dict[str, Any]] = {}


def get_index_state(dbname: str) -> Dict[str, Any]:
    """索引构建运行状态快照（含构建中实时耗时）。"""
    st = dict(_INDEX_STATE.get(dbname) or {})
    st.setdefault("state", "idle")
    if st.get("state") == "building" and st.get("started_at"):
        st["elapsed"] = round(time.time() - st["started_at"], 1)
    return st


# Per-database mutexes to prevent concurrent indexing of the same db_name from
# trampling each other (e.g. startup auto-summary racing with a manual /refresh
# call). When two indexing flows run for the same db, one path's
# delete_db_profile() can drop a chroma collection while the other path still
# has 1000s of writes queued against its UUID, producing
# ``Collection <uuid> does not exist`` errors for every chunk.
_DB_INDEX_LOCKS: Dict[str, threading.Lock] = {}
_DB_INDEX_LOCKS_GUARD = threading.Lock()


def _get_db_index_lock(dbname: str) -> threading.Lock:
    """Return (creating if needed) the per-db indexing mutex."""
    with _DB_INDEX_LOCKS_GUARD:
        lock = _DB_INDEX_LOCKS.get(dbname)
        if lock is None:
            lock = threading.Lock()
            _DB_INDEX_LOCKS[dbname] = lock
        return lock


class DBSummaryClient:
    """The client for DBSummary.

    DB Summary client, provide db_summary_embedding(put db profile and table profile
    summary into vector store), get_similar_tables method(get user query related tables
    info)

    Args:
        system_app (SystemApp): Main System Application class that manages the
            lifecycle and registration of components..
    """

    def __init__(self, system_app: SystemApp):
        """Create a new DBSummaryClient."""
        self.system_app = system_app

        self.app_config = self.system_app.config.configs.get("app_config")
        self.storage_config = self.app_config.rag.storage

    @property
    def embeddings(self) -> Embeddings:
        """Get the embeddings."""
        embedding_factory: EmbeddingFactory = self.system_app.get_component(
            "embedding_factory", component_type=EmbeddingFactory
        )
        return embedding_factory.create()

    def db_summary_embedding(self, dbname, db_type):
        """Put db profile and table profile summary into vector store.

        Serializes per-db so that concurrent triggers (startup auto-summary,
        ``/refresh`` API, retries on failure) cannot race and delete each
        other's in-flight chroma collections.
        """
        lock = _get_db_index_lock(dbname)
        if not lock.acquire(blocking=False):
            logger.info(
                f"{dbname} summary already in progress; "
                "skipping concurrent trigger to avoid race with in-flight writes."
            )
            return False
        _prev = _INDEX_STATE.get(dbname) or {}
        _INDEX_STATE[dbname] = {
            "state": "building",
            "started_at": time.time(),
            "last_success_at": _prev.get("last_success_at"),
            "last_error": None,
            # 保留上次条数：构建期间状态查询用缓存值，避免与 chroma 写操作争锁。
            "last_table_count": _prev.get("last_table_count"),
            "last_field_count": _prev.get("last_field_count"),
        }
        try:
            db_summary_client = self.create_summary_client(dbname, db_type)

            self.init_db_profile(db_summary_client, dbname)

            # 构建完成，记录最新条数（此时无并发写，count 安全且快）。
            try:
                _tc, _fc = self.get_index_counts(dbname)
            except Exception:  # noqa: BLE001
                _tc, _fc = None, None
            _INDEX_STATE[dbname].update(
                {
                    "state": "idle",
                    "finished_at": time.time(),
                    "last_success_at": time.time(),
                    "last_error": None,
                    "last_table_count": _tc,
                    "last_field_count": _fc,
                }
            )
            logger.info("db summary embedding success")
            return True
        except Exception as e:
            message = traceback.format_exc()
            _INDEX_STATE[dbname].update(
                {
                    "state": "idle",
                    "finished_at": time.time(),
                    "last_error": str(e)[:500],
                }
            )
            logger.warning(
                f"{dbname}, {db_type} summary error!{str(e)}, detail: {message}"
            )
            raise
        finally:
            lock.release()

    def get_index_counts(self, dbname) -> Tuple[int, int]:
        """(表级索引条数, 字段级索引条数)；集合不存在或读取失败时返回 0。"""
        table_connector, field_connector = self._get_vector_connector_by_db(dbname)
        return (
            int(getattr(table_connector, "count", lambda: 0)() or 0),
            int(getattr(field_connector, "count", lambda: 0)() or 0),
        )

    def get_db_summary(self, dbname, query, topk):
        """Get user query related tables info."""
        from dbgpt_ext.rag.retriever.db_schema import DBSchemaRetriever

        table_vector_connector, field_vector_connector = (
            self._get_vector_connector_by_db(dbname)
        )
        retriever = DBSchemaRetriever(
            top_k=topk,
            table_vector_store_connector=table_vector_connector,
            field_vector_store_connector=field_vector_connector,
            separator="--table-field-separator--",
        )

        table_docs = retriever.retrieve(query)
        ans = [d.content for d in table_docs]
        return ans

    def init_db_summary(self):
        """Initialize db summary profile."""
        local_db_manager = ConnectorManager.get_instance(self.system_app)
        db_mange = local_db_manager
        dbs = db_mange.get_db_list()
        for item in dbs:
            try:
                self.db_summary_embedding(item["db_name"], item["db_type"])
            except Exception as e:
                message = traceback.format_exc()
                logger.warning(
                    f"{item['db_name']}, {item['db_type']} summary error!{str(e)}, "
                    f"detail: {message}"
                )

    def init_db_profile(self, db_summary_client, dbname):
        """Initialize db summary profile.

        Args:
        db_summary_client(DBSummaryClient): DB Summary Client
        dbname(str): dbname
        """
        vector_store_name = dbname + "_profile"
        field_vector_store_name = dbname + "_profile_field"

        table_vector_connector, field_vector_connector = (
            self._get_vector_connector_by_db(dbname)
        )
        # 【修复·核心】原来只判断 table 集合存在(且有数据)就跳过重建，
        # 导致"表级索引有了、字段级索引永远为 0"的空壳残留。
        # 现改为：table 与 field 两个集合都非空才算建好，任一为空则重建；
        # 重建前用 truncate() 清残留（保留集合句柄，避免 delete_vector_name
        # 内部 clear_system_cache() 令缓存句柄全部失效 → "Collection ... does not exist"）。
        table_ready = table_vector_connector.vector_name_exists()
        field_ready = field_vector_connector.vector_name_exists()
        if table_ready and field_ready:
            logger.info(f"Vector store {vector_store_name} exists, skip init")
            logger.info("initialize db summary profile success...")
            return

        for connector, store_name in (
            (table_vector_connector, vector_store_name),
            (field_vector_connector, field_vector_store_name),
        ):
            try:
                connector.truncate()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"truncate {store_name} failed before rebuild: {e}")

        from dbgpt_ext.rag.assembler.db_schema import DBSchemaAssembler
        from dbgpt_ext.rag.summary.rdbms_db_summary import _DEFAULT_COLUMN_SEPARATOR

        chunk_parameters = ChunkParameters(
            text_splitter=RDBTextSplitter(
                column_separator=_DEFAULT_COLUMN_SEPARATOR,
                separator="--table-field-separator--",
            )
        )
        db_assembler = DBSchemaAssembler.load_from_connection(
            connector=db_summary_client.db,
            table_vector_store_connector=table_vector_connector,
            field_vector_store_connector=field_vector_connector,
            chunk_parameters=chunk_parameters,
            max_seq_length=self.app_config.service.web.embedding_model_max_seq_len,
        )

        if len(db_assembler.get_chunks()) > 0:
            db_assembler.persist()
        logger.info("initialize db summary profile success...")

    def delete_db_profile(self, dbname):
        """Delete db profile.

        Held under the per-db indexing lock so a delete cannot race with a
        concurrent embedding flow (which would otherwise see chroma drop the
        collection mid-write and fail every subsequent chunk with
        ``Collection <uuid> does not exist``).
        """
        lock = _get_db_index_lock(dbname)
        with lock:
            table_vector_store_name = dbname + "_profile"
            field_vector_store_name = dbname + "_profile_field"

            table_vector_connector, field_vector_connector = (
                self._get_vector_connector_by_db(dbname)
            )

            table_vector_connector.delete_vector_name(table_vector_store_name)
            field_vector_connector.delete_vector_name(field_vector_store_name)
            logger.info(f"delete db profile {dbname} success")

    @staticmethod
    def create_summary_client(dbname: str, db_type: str):
        """
        Create a summary client based on the database type.

        Args:
            dbname (str): The name of the database.
            db_type (str): The type of the database.
        """
        if "graph" in db_type:
            return GdbmsSummary(dbname, db_type)
        else:
            return RdbmsSummary(dbname, db_type)

    def _get_vector_connector_by_db(
        self, dbname
    ) -> Tuple[VectorStoreBase, VectorStoreBase]:
        vector_store_name = dbname + "_profile"
        storage_manager = StorageManager.get_instance(self.system_app)
        table_vector_store = storage_manager.create_vector_store(
            index_name=vector_store_name
        )
        field_vector_store_name = dbname + "_profile_field"
        field_vector_store = storage_manager.create_vector_store(
            index_name=field_vector_store_name
        )
        return table_vector_store, field_vector_store
