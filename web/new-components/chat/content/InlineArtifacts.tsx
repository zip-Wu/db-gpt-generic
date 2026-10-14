import { Button, Collapse } from 'antd';
import { DownloadOutlined, ExpandOutlined } from '@ant-design/icons';
import React, { memo, useMemo } from 'react';

import AdvancedChart, { createChartConfig } from '@/new-components/charts';
import { AutoHeightIframe } from './ManusRightPanel';
import type { ArtifactItem } from './ManusLeftPanel';

/**
 * 单栏对话布局（variant='single'）的结果渲染器。
 *
 * 双栏布局里结果都放在右侧面板，这里把每一轮的产物直接渲染在该轮回答的下方：
 * 报告 / 图表 / 图片就地展开（这才是用户要看的"结果"）；代码与附件默认折叠，
 * 它们是佐证材料而不是回答本身，展开会把对话顶出屏幕。
 *
 * 依赖：AdvancedChart（图表）、AutoHeightIframe（报告，已处理 iframe 高度塌陷）。
 */

export interface InlineArtifactsProps {
  artifacts: ArtifactItem[];
  onDownload?: (artifact: ArtifactItem) => void;
  /**
   * 单栏布局页：HTML 只给一段有限高度的预览，
   * 点击整块再展开到右侧栏 —— 报告整页内联会把消息流顶得很长。
   */
  compact?: boolean;
  /** compact 模式下点击预览区时回调（一般用它打开右侧栏）。 */
  onOpenRight?: (artifact: ArtifactItem) => void;
}

/** 报告正文：content 可能是 HTML 字符串，也可能是包装对象。 */
const readHtml = (content: any): string => {
  if (typeof content === 'string') return content;
  return content?.html || content?.content || '';
};

/** 图片地址：content 可能是 URL 字符串，也可能是包装对象。 */
const readImageUrl = (content: any): string => {
  if (typeof content === 'string') return content;
  return content?.url || content?.src || '';
};

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB'];

const formatSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return '';
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${SIZE_UNITS[unit]}`;
};

/** 就地展开的产物类型：它们是回答的一部分。 */
const INLINE_TYPES = new Set(['html', 'image', 'chart', 'table']);

const InlineArtifacts: React.FC<InlineArtifactsProps> = ({
  artifacts,
  onDownload,
  compact = false,
  onOpenRight,
}) => {
  const { inline, folded } = useMemo(() => {
    const inlineItems: ArtifactItem[] = [];
    const foldedItems: ArtifactItem[] = [];
    (artifacts || []).forEach(a => {
      (INLINE_TYPES.has(a.type) ? inlineItems : foldedItems).push(a);
    });
    return { inline: inlineItems, folded: foldedItems };
  }, [artifacts]);

  if (!artifacts || artifacts.length === 0) return null;

  return (
    <div className='mt-4 flex flex-col gap-3'>
      {inline.map(artifact => (
        <div
          key={artifact.id}
          className='overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-[#17181c]'
        >
          {artifact.type === 'html' &&
            (compact ? (
              <button
                type='button'
                onClick={() => onOpenRight?.(artifact)}
                title='点击查看完整报告'
                className='group relative block w-full cursor-pointer text-left'
              >
                {/* 预览只读：iframe 不吃点击事件，整块才是按钮。 */}
                <div className='pointer-events-none max-h-[300px] overflow-hidden bg-white'>
                  <AutoHeightIframe
                    srcDoc={readHtml(artifact.content)}
                    title={artifact.name}
                    minHeight={300}
                  />
                </div>
                <div className='pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-center gap-1.5 bg-gradient-to-t from-white via-white/95 to-transparent pb-3 pt-10 text-xs font-medium text-blue-600 dark:from-[#17181c] dark:via-[#17181c]/95 dark:text-blue-400'>
                  <ExpandOutlined className='text-[11px]' />
                  点击查看完整报告
                </div>
              </button>
            ) : (
              <AutoHeightIframe
                srcDoc={readHtml(artifact.content)}
                title={artifact.name}
                minHeight={360}
              />
            ))}

          {artifact.type === 'image' && (
            <img
              src={readImageUrl(artifact.content)}
              alt={artifact.name}
              className='h-auto w-full'
            />
          )}

          {artifact.type === 'chart' && (
            <div className='h-80 p-3'>
              <AdvancedChart
                config={createChartConfig(artifact.content?.data || [], {
                  chartType: artifact.content?.chartType || 'line',
                  xField: artifact.content?.xField || 'x',
                  yField: artifact.content?.yField || 'y',
                  seriesField: artifact.content?.seriesField,
                  title: artifact.content?.title,
                  smooth: true,
                  height: 300,
                })}
              />
            </div>
          )}
        </div>
      ))}

      {folded.length > 0 && (
        <Collapse
          ghost
          size='small'
          items={folded.map(artifact => ({
            key: artifact.id,
            label: (
              <span className='text-sm text-gray-600 dark:text-gray-300'>
                {artifact.name || '附件'}
                {artifact.size ? (
                  <span className='ml-2 text-xs text-gray-400'>{formatSize(artifact.size)}</span>
                ) : null}
              </span>
            ),
            children: (
              <div className='flex flex-col gap-2'>
                <pre className='m-0 max-h-96 overflow-auto rounded-lg bg-gray-50 p-3 text-xs leading-relaxed dark:bg-[#0f1013]'>
                  {artifact.type === 'code'
                    ? String(artifact.content)
                    : readHtml(artifact.content) || artifact.name}
                </pre>
                {onDownload && (
                  <div>
                    <Button size='small' icon={<DownloadOutlined />} onClick={() => onDownload(artifact)}>
                      下载
                    </Button>
                  </div>
                )}
              </div>
            ),
          }))}
        />
      )}
    </div>
  );
};

export default memo(InlineArtifacts);
