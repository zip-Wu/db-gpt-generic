import { message } from 'antd';

/**
 * 复制文本到系统剪贴板（带 http 环境回退）。
 *
 * 背景：部署常见形态是以 `http://<内网IP>:<端口>` 提供服务，**不是安全上下文**
 * （`window.isSecureContext === false`）→ `navigator.clipboard` 不存在，
 * 直接调用会抛异常、内容也不会进剪贴板（曾表现为"点了 Copy 没反应"）。
 * 故统一走此函数：能用 Clipboard API 就用，否则回退
 * textarea + `document.execCommand('copy')`（http 下可用）。
 */
export const copyTextToClipboard = async (text: string): Promise<boolean> => {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的回退路径
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '0';
    textarea.style.left = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);

    const selection = document.getSelection();
    const previousRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;

    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const ok = document.execCommand('copy');

    document.body.removeChild(textarea);
    if (selection && previousRange) {
      selection.removeAllRanges();
      selection.addRange(previousRange);
    }
    return ok;
  } catch {
    return false;
  }
};

/** 带提示的复制：成功/失败都有明确反馈（原实现无论成败都提示成功）。 */
export const copyWithToast = async (
  text: string,
  successText = '已复制到剪贴板',
  failText = '复制失败，请手动选中后复制',
): Promise<boolean> => {
  const ok = await copyTextToClipboard(text);
  if (ok) message.success(successText);
  else message.error(failText);
  return ok;
};
