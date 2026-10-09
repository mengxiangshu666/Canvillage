import { useSyncExternalStore } from 'react';

function readRatio(): number {
  if (typeof window === 'undefined') return 1;
  const ratio = Number(window.devicePixelRatio);
  return Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
}

function subscribe(onStoreChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {};
  const query = window.matchMedia(`(resolution: ${readRatio()}dppx)`);
  const listener = () => onStoreChange();
  query.addEventListener?.('change', listener);
  window.addEventListener('resize', listener, { passive: true });
  return () => {
    query.removeEventListener?.('change', listener);
    window.removeEventListener('resize', listener);
  };
}

export function useDevicePixelRatio(): number {
  return useSyncExternalStore(subscribe, readRatio, () => 1);
}
