import { extractStyle } from '@ant-design/cssinjs';
import type Entity from '@ant-design/cssinjs/lib/Cache';
import { createHash } from 'crypto';
import fs from 'fs';
import path from 'path';

export type DoExtraStyleOptions = {
  cache: Entity;
  dir?: string;
  baseFileName?: string;
};
export function doExtraStyle({ cache, dir = 'antd-output', baseFileName = 'antd.min' }: DoExtraStyleOptions) {
  const baseDir = path.resolve(__dirname, '../../static/css');

  const outputCssPath = path.join(baseDir, dir);

  if (!fs.existsSync(outputCssPath)) {
    fs.mkdirSync(outputCssPath, { recursive: true });
  }

  const css = extractStyle(cache, true);
  if (!css) return '';

  const md5 = createHash('md5');
  const hash = md5.update(css).digest('hex');
  const fileName = `${baseFileName}.${hash.substring(0, 8)}.css`;
  const fullpath = path.join(outputCssPath, fileName);

  const res = `_next/static/css/${dir}/${fileName}`;

  if (fs.existsSync(fullpath)) return res;

  // 并发写入保护：next build 会并行预渲染全部页面，每个页面都会走到这里。
  // 由于 extractStyle 的结果在多数页面上相同，多个 worker 会同时写同一个
  // 文件名；在 Windows 上这会让后到的写操作抛 EPERM。
  // 处理方式：写入失败不中断构建——文件已存在说明别的 worker 写成功了，
  // 直接复用即可；否则短延迟后重试一次。
  try {
    fs.writeFileSync(fullpath, css);
  } catch (e) {
    if (fs.existsSync(fullpath)) return res;
    try {
      // 极短退避后重试，避开同时写入的窗口
      const deadline = Date.now() + 200;
      while (Date.now() < deadline) {
        /* 忙等，构建期一次性开销，可接受 */
      }
      fs.writeFileSync(fullpath, css);
    } catch (e2) {
      // 仍失败才真正报错，避免掩盖其他问题
      throw e2;
    }
  }

  return res;
}
