import { useEffect } from "react";
import styles from "./intro-ritual-screen.module.css";

export function IntroRitualScreen({
  onComplete,
  reducedMotion,
}: {
  onComplete: () => void;
  reducedMotion: boolean;
}) {
  useEffect(() => {
    if (reducedMotion) {
      onComplete();
      return;
    }

    const timer = window.setTimeout(onComplete, 5440);
    return () => window.clearTimeout(timer);
  }, [onComplete, reducedMotion]);

  if (reducedMotion) return null;

  return (
    <section
      className={styles.layer}
      aria-label="村长无限画布介绍"
      onClick={onComplete}
      onWheel={onComplete}
    >
      <div className={styles.aperture} aria-hidden="true" />
      <div className={styles.scanline} aria-hidden="true" />
      <div className={styles.focusFrame} aria-hidden="true">
        <div className={styles.hudTopLeft}>A001_C012 · VILLAGE CANVAS</div>
        <div className={styles.hudTopRight}>TC 00:00:00:10</div>
        <div className={styles.hudBottomLeft}>
          <span className={styles.recDot} />
          REC
        </div>
        <div className={styles.hudBottomRight}>4K · 24FPS</div>
        <img
          className={styles.brandMark}
          src="/brand/village-canvas-mark-light.png"
          alt=""
          aria-hidden="true"
          draggable={false}
        />
      </div>
      <div className={styles.focusCore} aria-hidden="true" />
      <div className={styles.shutter} aria-hidden="true" />
    </section>
  );
}
