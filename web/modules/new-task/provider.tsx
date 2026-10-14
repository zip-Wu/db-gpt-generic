/** React facade for the app-wide new-task command. */

import { useRouter } from 'next/router';
import React, { createContext, useCallback, useContext, useEffect, useRef } from 'react';

import type { NewTaskCoordinator, NewTaskGuard } from './coordinator';
import { createNewTaskCoordinator } from './coordinator';

const NewTaskContext = createContext<NewTaskCoordinator | null>(null);
const KeepPathContext = createContext<((keep: boolean) => void) | null>(null);

function useCoordinator(): NewTaskCoordinator {
  const coordinator = useContext(NewTaskContext);
  if (!coordinator) {
    throw new Error('New-task hooks must be used within NewTaskProvider.');
  }
  return coordinator;
}

export function NewTaskProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  // 独立/嵌入式对话页（Playground 的独立变体）通过
  // useKeepCurrentPath(true) 声明：执行“新建任务/清除会话”后必须留在当前路径，
  // 且不做任何 URL 改写 —— 内嵌（iframe）时地址由宿主页面把控，多一次路由替换
  // 就可能让整页重新挂载成完整首页（侧边栏、首页文案全部冒出来）。
  // TODO(business): 若你新增了自己的独立对话页路由（如 /standalone/chat），
  // 把前缀加进 STANDALONE_CHAT_PREFIXES，/app.tsx 的 hideSidebar 同步。
  const keepPathRef = useRef(false);
  const setKeepPath = useCallback((keep: boolean) => {
    keepPathRef.current = keep;
  }, []);

  const coordinatorRef = useRef<NewTaskCoordinator | null>(null);
  if (!coordinatorRef.current) {
    const getBrowserLocation = () => {
      if (typeof window === 'undefined') return null;
      return {
        pathname: window.location.pathname.replace(/\/$/, '') || '/',
        hasQueryOrHash: Boolean(window.location.search || window.location.hash),
      };
    };

    /** 独立对话页：允许携带 query/hash，且不触发任何 URL 替换。 */
    const STANDALONE_CHAT_PREFIXES: string[] = ['/standalone'];
    const toleratesQuery = (path: string) =>
      keepPathRef.current ||
      STANDALONE_CHAT_PREFIXES.some(p => path.startsWith(p));

    const getCanonicalPath = () => {
      const browserLocation = getBrowserLocation();
      const currentPathname = browserLocation?.pathname ?? routerRef.current.pathname;
      // 独立对话页在执行“新建任务/清除会话”后必须留在原路径。若写死成 '/'，
      // 整页会 remount 成完整首页——侧边栏和首页文案会全部冒出来。
      // 独立对话页自己声明过 → 原样保持；否则命中独立页前缀也保持；其余回到 '/'。
      if (keepPathRef.current) return currentPathname;
      return STANDALONE_CHAT_PREFIXES.some(p => currentPathname.startsWith(p))
        ? currentPathname
        : '/';
    };

    coordinatorRef.current = createNewTaskCoordinator({
      isCanonicalHome: () => {
        const browserLocation = getBrowserLocation();
        if (browserLocation) {
          const canonical = getCanonicalPath();
          if (browserLocation.pathname !== canonical) return false;
          // 独立对话页允许携带 query/hash（内嵌时常带 token 等参数），
          // 直接视为 canonical —— 不触发任何 URL 替换。
          return toleratesQuery(canonical) || !browserLocation.hasQueryOrHash;
        }

        const current = routerRef.current;
        const pathWithoutHash = current.asPath.split('#', 1)[0];
        const hasQuery = pathWithoutHash.includes('?');
        const currentPath = pathWithoutHash.split('?', 1)[0].replace(/\/$/, '') || '/';
        if (currentPath !== getCanonicalPath()) return false;
        return toleratesQuery(currentPath) || !hasQuery;
      },
      goCanonicalHome: async () => {
        const current = routerRef.current;
        const targetPath = getCanonicalPath();
        await current.replace(targetPath, undefined, { shallow: current.pathname === targetPath });
      },
    });
  }

  return (
    <NewTaskContext.Provider value={coordinatorRef.current}>
      <KeepPathContext.Provider value={setKeepPath}>{children}</KeepPathContext.Provider>
    </NewTaskContext.Provider>
  );
}

/** Return the single app-wide command used by every "new task" affordance. */
export function useStartNewTask(): () => Promise<void> {
  const coordinator = useCoordinator();
  return useCallback(() => coordinator.begin(), [coordinator]);
}

/** Attach the mounted task workspace that owns the reset implementation. */
export function useNewTaskOwner(resetForNewTask: () => void): void {
  const coordinator = useCoordinator();
  const resetRef = useRef(resetForNewTask);
  resetRef.current = resetForNewTask;

  useEffect(() => coordinator.attach(() => resetRef.current()), [coordinator]);
}

/**
 * Attach a guard consulted before every command. Resolve false to cancel the
 * command (e.g. while a turn is in flight and the user declines the
 * interruption confirmation).
 */
export function useNewTaskGuard(guard: NewTaskGuard): void {
  const coordinator = useCoordinator();
  const guardRef = useRef(guard);
  guardRef.current = guard;

  useEffect(() => coordinator.attachGuard(() => guardRef.current()), [coordinator]);
}

/**
 * 独立/嵌入式对话页声明：执行“新建任务/清除会话”后保持当前路径。
 * Playground 的独立变体（variant='single' 等）调用。
 */
export function useKeepCurrentPath(keep: boolean): void {
  const setKeepPath = useContext(KeepPathContext);
  useEffect(() => {
    if (!setKeepPath) return;
    setKeepPath(keep);
    return () => setKeepPath(false);
  }, [keep, setKeepPath]);
}

