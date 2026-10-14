import { format } from 'sql-formatter';

/** Theme */
export const STORAGE_THEME_KEY = '__db_gpt_theme_key';
/** Language */
export const STORAGE_LANG_KEY = '__db_gpt_lng_key';
/** Init Message */
export const STORAGE_INIT_MESSAGE_KET = '__db_gpt_im_key';
/** Flow nodes */
export const FLOW_NODES_KEY = '__db_gpt_static_flow_nodes_key';

export function formatSql(sql: string, lang?: string) {
  if (!sql) return '';
  try {
    return format(sql, { language: lang });
  } catch {
    return sql;
  }
}

// Utility function to transform dbgpt-fs:// URLs to HTTP service URLs
export const transformFileUrl = (url: string): string => {
  try {
    if (!url.startsWith('dbgpt-fs://')) {
      return url;
    }

    // Parse the dbgpt-fs:// URL structure
    const parsedUrl = new URL(url);

    if (parsedUrl.protocol !== 'dbgpt-fs:') {
      return url;
    }

    // const storageType = parsedUrl.hostname;
    const pathParts = parsedUrl.pathname.split('/').filter(Boolean);

    if (pathParts.length < 2) {
      return url; // Not enough path parts
    }

    const bucket = pathParts[0];
    const fileId = pathParts[1];

    // Transform to service URL
    // Using process.env.API_BASE_URL as the base
    return `${process.env.API_BASE_URL || ''}/api/v2/serve/file/files/${bucket}/${fileId}${parsedUrl.search}`;
  } catch (e) {
    console.error('Error transforming file URL:', e);
    return url; // Return original URL if transformation fails
  }
};

// Parse resourceValue to get the resource array
export const parseResourceValue = (value: any): any[] => {
  // Return empty array if value is empty string or undefined
  if (!value || value === 'undefined' || value === 'null') {
    return [];
  }

  try {
    // If the value is a string, try to parse it as JSON
    // Plain strings (e.g. knowledge space names like "知识库1") are not valid JSON,
    // skip JSON.parse to avoid noisy console errors
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (!trimmed.startsWith('[') && !trimmed.startsWith('{')) {
        return [];
      }
    }
    let resourceData = typeof value === 'string' ? JSON.parse(value) : value;

    // If resourceData is not an array but an object, convert it to array format
    if (resourceData && !Array.isArray(resourceData) && typeof resourceData === 'object') {
      // If it has file_name or file_path, convert to appropriate type
      if (resourceData.file_name || resourceData.file_path) {
        const fileName = resourceData.file_name || '';
        const filePath = resourceData.file_path || resourceData.url || '';

        const isImage = /\.(jpg|jpeg|png|gif|bmp|webp|svg)$/i.test(fileName);
        const isAudio = /\.(mp3|wav|ogg|aac|flac|m4a)$/i.test(fileName);
        const isVideo = /\.(mp4|webm|mov|avi|wmv|flv|mkv)$/i.test(fileName);

        if (isImage) {
          resourceData = [
            {
              type: 'image_url',
              image_url: {
                url: filePath,
                fileName: fileName,
              },
            },
          ];
        } else if (isAudio) {
          resourceData = [
            {
              type: 'audio_url',
              audio_url: {
                url: filePath,
                file_name: fileName,
              },
            },
          ];
        } else if (isVideo) {
          resourceData = [
            {
              type: 'video_url',
              video_url: {
                url: filePath,
                file_name: fileName,
              },
            },
          ];
        } else {
          // Other file types
          resourceData = [
            {
              type: 'file_url',
              file_url: {
                url: filePath,
                file_name: fileName,
              },
            },
          ];
        }
      } else {
        resourceData = [resourceData];
      }
    } else if (!Array.isArray(resourceData)) {
      return [];
    }

    return resourceData;
  } catch (error) {
    console.error('Parse resourceValue error:', error);
    return [];
  }
};

/**
 * 去掉会话标题开头的数据库前缀（提问时前端自动带上，如 "[Database: LRSDB] "），
 * 列表展示用；原数据不动。
 */
export function stripDbPrefix(title?: string) {
  return (title ?? '').replace(/^\[Database:[^\]]*\]\s*/, '').trim();
}

/**
 * 会话时间在库里是 UTC（"2026-10-12 11:07:11"），页面展示北京时间（+8h）。
 * 非法输入原样返回。
 */
export function utcToBeijing(utcStr?: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(utcStr ?? '');
  if (!m) return utcStr ?? '';
  const t = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) + 8 * 3600 * 1000;
  const d = new Date(t);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(
    d.getUTCMinutes(),
  )}:${p(d.getUTCSeconds())}`;
}

export * from './constants';
export * from './storage';
