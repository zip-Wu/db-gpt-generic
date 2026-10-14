import {
  ConsoleSqlOutlined,
  CopyOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { Tooltip } from 'antd';
import React from 'react';
import { copyWithToast } from './clipboard';

/**
 * SQL 展示卡片（与右栏「SQL Query」卡片同款）。
 *
 * 抽取自 ManusRightPanel 的内联实现，供单栏布局在左侧步骤卡片下就地展开使用；
 * 右栏保持原实现不动，避免影响已验收的展示效果。
 */

export const getDbTypeInfo = (dbType?: string): { icon: React.ReactNode; label: string } => {
  if (!dbType) return { icon: <DatabaseOutlined className='text-gray-500 text-sm' />, label: 'Database' };
  const lower = dbType.toLowerCase();
  if (lower.includes('mysql'))
    return { icon: <ConsoleSqlOutlined className='text-blue-500 text-sm' />, label: 'MySQL' };
  if (lower.includes('postgre'))
    return { icon: <DatabaseOutlined className='text-blue-400 text-sm' />, label: 'PostgreSQL' };
  if (lower.includes('sqlite'))
    return { icon: <DatabaseOutlined className='text-amber-500 text-sm' />, label: 'SQLite' };
  if (lower.includes('mongo'))
    return { icon: <DatabaseOutlined className='text-green-500 text-sm' />, label: 'MongoDB' };
  if (lower.includes('oracle')) return { icon: <DatabaseOutlined className='text-red-500 text-sm' />, label: 'Oracle' };
  if (lower.includes('mssql') || lower.includes('sqlserver'))
    return { icon: <DatabaseOutlined className='text-indigo-500 text-sm' />, label: 'SQL Server' };
  return { icon: <DatabaseOutlined className='text-gray-500 text-sm' />, label: dbType };
};

const KEYWORDS =
  /^\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|IS|NULL|AS|CASE|WHEN|THEN|ELSE|END|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|COUNT|SUM|AVG|MIN|MAX|COALESCE|CAST|DESC|ASC)\b/i;

export const highlightSQL = (sqlStr: string): React.ReactNode[] => {
  const parts: { text: string; type: 'keyword' | 'string' | 'number' | 'plain' }[] = [];
  let remaining = sqlStr;
  let safetyCounter = 0;

  while (remaining.length > 0 && safetyCounter < 10000) {
    safetyCounter++;
    const strMatch = remaining.match(/^('[^']*')/);
    if (strMatch) {
      parts.push({ text: strMatch[1], type: 'string' });
      remaining = remaining.slice(strMatch[1].length);
      continue;
    }
    const kwMatch = remaining.match(KEYWORDS);
    if (kwMatch) {
      parts.push({ text: kwMatch[1].toUpperCase(), type: 'keyword' });
      remaining = remaining.slice(kwMatch[1].length);
      continue;
    }
    const numMatch = remaining.match(/^\b(\d+\.?\d*)\b/);
    if (numMatch) {
      parts.push({ text: numMatch[1], type: 'number' });
      remaining = remaining.slice(numMatch[1].length);
      continue;
    }
    parts.push({ text: remaining[0], type: 'plain' });
    remaining = remaining.slice(1);
  }

  return parts.map((p, i) => {
    switch (p.type) {
      case 'keyword':
        return (
          <span key={i} className='text-[#569cd6] font-semibold'>
            {p.text}
          </span>
        );
      case 'string':
        return (
          <span key={i} className='text-[#ce9178]'>
            {p.text}
          </span>
        );
      case 'number':
        return (
          <span key={i} className='text-[#b5cea8]'>
            {p.text}
          </span>
        );
      default:
        return <span key={i}>{p.text}</span>;
    }
  });
};

export interface SqlSourceStep {
  action?: string;
  actionInput?: unknown;
  description?: string;
}

/**
 * 从执行步骤中取出 SQL 文本。
 * 与右栏逻辑一致：先取 actionInput.sql，再回退到 description 里的 `Action Input: {...}`。
 */
export const extractSqlFromStep = (step: SqlSourceStep): string => {
  let sql = '';

  if (step.actionInput) {
    try {
      const parsed =
        typeof step.actionInput === 'string' ? JSON.parse(step.actionInput) : (step.actionInput as { sql?: string });
      sql = parsed?.sql || '';
    } catch {
      const rawMatch = String(step.actionInput).match(/"sql"\s*:\s*"([\s\S]*?)"/);
      if (rawMatch) sql = rawMatch[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
    }
  }

  if (!sql && step.description) {
    const inputMatch = step.description.match(/Action Input:\s*({[\s\S]*?})(?:\n|$)/);
    if (inputMatch) {
      try {
        const parsed = JSON.parse(inputMatch[1]);
        sql = parsed.sql || '';
      } catch {
        const rawMatch = inputMatch[1].match(/"sql"\s*:\s*"([\s\S]*?)"/);
        if (rawMatch) sql = rawMatch[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
      }
    }
  }

  return sql;
};

export interface SqlQueryCardProps {
  sql: string;
  databaseType?: string;
  databaseName?: string;
  /** 单栏内联展开用：限高更小、字号略小。 */
  compact?: boolean;
}

const SqlQueryCard: React.FC<SqlQueryCardProps> = ({ sql, databaseType, databaseName, compact = false }) => {
  return (
    <div className='rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden bg-white dark:bg-[#1a1b1e]'>
      {/* Header bar */}
      <div className='flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-[#252629] border-b border-gray-200 dark:border-gray-700'>
        <div className='flex items-center gap-2'>
          {getDbTypeInfo(databaseType).icon}
          <span className='text-xs font-semibold text-gray-600 dark:text-gray-300'>SQL Query</span>
          {databaseType && (
            <span className='text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 font-medium'>
              {getDbTypeInfo(databaseType).label}
            </span>
          )}
          {databaseName && (
            <span className='text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 font-medium'>
              {databaseName}
            </span>
          )}
          <span className='text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400 font-medium'>
            READ ONLY
          </span>
        </div>
        <Tooltip title='复制SQL'>
          <button
            className='flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700'
            onClick={async e => {
              e.stopPropagation();
              await copyWithToast(sql, 'SQL已复制到剪贴板');
            }}
          >
            <CopyOutlined className='text-xs' />
            <span>Copy</span>
          </button>
        </Tooltip>
      </div>
      {/* SQL code area */}
      <div
        className='bg-[#1e1e2e] dark:bg-[#0d0d11] overflow-auto'
        style={{ maxHeight: compact ? 220 : 400 }}
      >
        <pre
          className={`${compact ? 'text-[12px] leading-5' : 'text-[13px] leading-6'} font-mono text-gray-200 p-4 m-0 whitespace-pre-wrap break-words`}
        >
          <code>{highlightSQL(sql)}</code>
        </pre>
      </div>
    </div>
  );
};

export default SqlQueryCard;
