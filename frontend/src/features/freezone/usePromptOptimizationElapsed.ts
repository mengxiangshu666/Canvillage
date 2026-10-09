import { useEffect, useState } from "react";

/** Visible elapsed time prevents an async optimizer request from looking frozen. */
export function usePromptOptimizationElapsed(active: boolean): number {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (!active) {
      setElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  return elapsedSeconds;
}

export function promptOptimizationProgressLabel(elapsedSeconds: number): string {
  if (elapsedSeconds < 2) return "读取模型与知识";
  if (elapsedSeconds < 20) return `AI 改写 ${elapsedSeconds}s`;
  return `模型仍在处理 ${elapsedSeconds}s`;
}
