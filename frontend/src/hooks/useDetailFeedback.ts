import { useCallback, useEffect, useState } from 'react';

import type { DetailLoadTarget, NoticeState } from '../types/ui';

export function useDetailFeedback() {
  const [detailLoading, setDetailLoading] = useState<DetailLoadTarget>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const beginDetailLoad = useCallback((target: Exclude<DetailLoadTarget, null>) => {
    setDetailLoading(target);
    setDetailError(null);
  }, []);

  const finishDetailLoad = useCallback(() => {
    setDetailLoading(null);
  }, []);

  const failDetailLoad = useCallback((message: string) => {
    setDetailLoading(null);
    setDetailError(message);
  }, []);

  const clearDetailError = useCallback(() => {
    setDetailError(null);
  }, []);

  return {
    detailLoading,
    detailError,
    notice,
    setNotice,
    beginDetailLoad,
    finishDetailLoad,
    failDetailLoad,
    clearDetailError,
  };
}
