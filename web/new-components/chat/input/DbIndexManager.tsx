import { Button, Modal, Spin, Tag } from 'antd';
import {
  CheckCircleFilled,
  DatabaseOutlined,
  ExclamationCircleFilled,
  SyncOutlined,
} from '@ant-design/icons';
import axios from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';

import { sendSpacePostRequest } from '@/utils/request';

/**
 * 问数索引维护面板（挂在聊天输入工具栏，数据源选择器旁）。
 *
 * 索引 = 表结构概要向量（{库名}_profile / _profile_field 两个 Chroma 集合）。
 * 系统靠它自动找表（schema-linking）；它没有内容变更检测——库结构变化
 * （新增表 / 改字段注释）后必须手动重建。本组件让使用者自己完成这件事：
 *   · 状态查询：GET  /api/v1/chat/db_index_status（表/字段条数、上次构建、最近错误）
 *   · 一键刷新：POST /api/v1/chat/refresh_db_index（后端删旧集合并异步重建）
 *   · 进度：每 2s 轮询状态，展示"已用时长"，完成后展示新条数。
 *
 * 请求方式说明：
 *   · 状态轮询用【裸 axios】（相对路径直发 + 自带 timeout）。不用项目封装的
 *     sendGetRequest——它会在 url 前再拼一层 /api，且失败时弹全局 toast；
 *     轮询请求应静默处理、错误只显示在本面板里。
 *   · 刷新操作用 sendSpacePostRequest（原样 url，失败 toast 便于立即感知）。
 */

interface IndexStatus {
  database_name: string;
  state: 'idle' | 'building';
  elapsed?: number;
  last_success_at?: number;
  last_error?: string | null;
  table_count: number;
  field_count: number;
}

const POLL_INTERVAL_MS = 2000;
/** 轮询超时（构建超过 10 分钟仍未结束视为异常，停止轮询提示用户）。 */
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

function formatTime(ts?: number | null): string {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return '—';
  }
}

export default function DbIndexManager({ dbName }: { dbName?: string | null }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'starting' | 'building' | 'done' | 'failed'>('idle');
  const [startedAt, setStartedAt] = useState<number>(0);
  const [buildResult, setBuildResult] = useState<string>('');
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const seenBuildingRef = useRef(false);

  const fetchStatus = useCallback(async (): Promise<IndexStatus | null> => {
    if (!dbName) return null;
    try {
      // 裸 axios：相对路径直发（同域部署），失败静默由调用方处理，不弹全局提示。
      const resp = await axios.get('/api/v1/chat/db_index_status', {
        params: { database_name: dbName },
        timeout: 10000,
      });
      const st: IndexStatus | null = resp?.data?.data ?? null;
      if (st) setStatus(st);
      return st;
    } catch (e) {
      return null;
    }
  }, [dbName]);

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    seenBuildingRef.current = false;
    const t0 = Date.now();
    timerRef.current = setInterval(async () => {
      const st = await fetchStatus();
      const elapsedMs = Date.now() - t0;
      if (!st) {
        if (elapsedMs > POLL_TIMEOUT_MS) {
          stopPolling();
          setPhase('failed');
          setBuildResult('状态查询超时，请关闭窗口后重试。');
        }
        return;
      }
      if (st.state === 'building') {
        seenBuildingRef.current = true;
        setPhase('building');
        return;
      }
      // state=idle：构建前的短暂空档（还没真正开始）继续等；已见过 building 则判定完成。
      if (!seenBuildingRef.current) {
        if (elapsedMs > 15000) {
          // 15s 都没进入 building：多半是提交/执行异常，停轮询按当前状态展示
          stopPolling();
          setRefreshing(false);
          if (st.last_error) {
            setPhase('failed');
            setBuildResult(st.last_error);
          } else {
            setPhase('idle');
          }
        }
        return;
      }
      stopPolling();
      setRefreshing(false);
      if (st.last_error) {
        setPhase('failed');
        setBuildResult(st.last_error);
      } else {
        setPhase('done');
        setBuildResult(
          `索引已更新：表 ${st.table_count} 条 / 字段 ${st.field_count} 条`,
        );
      }
    }, POLL_INTERVAL_MS);
  }, [fetchStatus, stopPolling]);

  useEffect(() => {
    if (open) {
      fetchStatus();
    } else {
      // 关闭时停轮询（构建在服务端继续，下次打开会看到新状态）
      stopPolling();
    }
    return () => stopPolling();
  }, [open, fetchStatus, stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const handleRefresh = useCallback(async () => {
    if (!dbName) return;
    setRefreshing(true);
    setPhase('starting');
    setStartedAt(Date.now());
    setBuildResult('');
    try {
      const data = await sendSpacePostRequest('/api/v1/chat/refresh_db_index', {
        database_name: dbName,
      });
      const started = data?.data?.started;
      if (started === false) {
        // 服务端已在构建（或稍早有人点过）：直接进轮询
        seenBuildingRef.current = true;
        setPhase('building');
      }
      startPolling();
    } catch (e: any) {
      setRefreshing(false);
      setPhase('failed');
      setBuildResult(e?.response?.data?.err_msg || e?.message || '刷新请求失败');
    }
  }, [dbName, startPolling]);

  const building = refreshing || phase === 'starting' || phase === 'building';
  const elapsedSec =
    building && startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0;
  // 上面这行只在渲染时取一次近似值；用 1s 心跳让"已用时长"走字
  const [, forceTick] = useState(0);
  useEffect(() => {
    if (!building) return;
    const t = setInterval(() => forceTick(v => v + 1), 1000);
    return () => clearInterval(t);
  }, [building]);

  const healthy =
    !building && status && !status.last_error && (status.table_count || 0) > 0;

  return (
    <>
      <Button
        type='text'
        size='small'
        icon={<DatabaseOutlined />}
        onClick={() => setOpen(true)}
        className='text-gray-600'
      >
        索引维护
      </Button>

      <Modal
        title='问数索引维护'
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={560}
        destroyOnClose={false}
      >
        {/* 状态区 */}
        <div className='mb-4 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3'>
          <div className='flex items-center justify-between'>
            <div className='text-sm text-gray-600'>索引状态</div>
            {building ? (
              <Tag color='processing' icon={<SyncOutlined spin />}>
                正在重建
              </Tag>
            ) : healthy ? (
              <Tag color='success' icon={<CheckCircleFilled />}>
                正常
              </Tag>
            ) : status?.last_error ? (
              <Tag color='error' icon={<ExclamationCircleFilled />}>
                上次失败
              </Tag>
            ) : (
              <Tag color='default'>未建索引</Tag>
            )}
          </div>
          <div className='mt-2 grid grid-cols-3 gap-2 text-sm'>
            <div>
              <div className='text-gray-400 text-xs'>表索引</div>
              <div className='font-medium'>{status ? status.table_count : '—'} 条</div>
            </div>
            <div>
              <div className='text-gray-400 text-xs'>字段索引</div>
              <div className='font-medium'>{status ? status.field_count : '—'} 条</div>
            </div>
            <div>
              <div className='text-gray-400 text-xs'>上次更新</div>
              <div className='font-medium'>{formatTime(status?.last_success_at)}</div>
            </div>
          </div>

          {building && (
            <div className='mt-3 flex items-center gap-2 text-sm text-blue-600'>
              <Spin size='small' />
              <span>
                正在重建索引…已用 {elapsedSec} 秒（通常一到三分钟，请保持页面打开）
              </span>
            </div>
          )}
          {phase === 'done' && (
            <div className='mt-3 flex items-center gap-2 text-sm text-green-600'>
              <CheckCircleFilled />
              <span>{buildResult}</span>
            </div>
          )}
          {phase === 'failed' && (
            <div className='mt-3 text-sm text-red-500 break-all'>
              <ExclamationCircleFilled className='mr-1' />
              {buildResult || '刷新失败'}
            </div>
          )}
        </div>

        {/* 说明区 */}
        <div className='space-y-2 text-sm text-gray-600 leading-relaxed'>
          <div className='font-medium text-gray-700'>为什么要刷新索引？</div>
          <p>
            系统通过「问数索引」自动识别数据库的表结构。数据库结构变化后
            （例如<span className='text-gray-800'>新增了表</span>、或
            <span className='text-gray-800'>修改了字段的中文说明</span>），
            刷新索引后系统才能把新内容用起来。
          </p>
          <div className='font-medium text-gray-700'>什么时候需要刷新？</div>
          <ul className='list-disc pl-5 space-y-1'>
            <li>数据库新增了表；</li>
            <li>修改过表的字段说明（注释）；</li>
            <li>更换了 Embedding（向量化）模型。</li>
          </ul>
          <div className='font-medium text-gray-700'>刷新会做什么？</div>
          <p>
            仅重建“表结构说明”的索引，<span className='text-gray-800'>不读取、不修改任何业务数据</span>，
            也不影响数据库本身。刷新期间问答功能可以正常使用，索引更新在完成后生效。
          </p>
        </div>

        {/* 操作区 */}
        <div className='mt-5 flex items-center justify-end gap-3'>
          <Button onClick={() => setOpen(false)}>关闭</Button>
          <Button
            type='primary'
            icon={<SyncOutlined />}
            loading={building}
            disabled={!dbName || building}
            onClick={handleRefresh}
          >
            {phase === 'failed' ? '重试刷新' : '刷新索引'}
          </Button>
        </div>
        {!dbName && (
          <div className='mt-2 text-xs text-gray-400 text-right'>
            未选择数据源，请先在上方选择数据库。
          </div>
        )}
      </Modal>
    </>
  );
}
