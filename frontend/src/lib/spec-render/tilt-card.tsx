"use client";

import { useRef, useState, type ReactNode } from "react";
import {
  motion,
  useMotionValue,
  useSpring,
  useTransform,
  useMotionTemplate,
  AnimatePresence,
} from "framer-motion";

const SPRING_CONFIG = { stiffness: 150, damping: 20, mass: 0.5 };

export function TiltCard({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);

  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const mouseXPx = useMotionValue(0);
  const mouseYPx = useMotionValue(0);

  const mouseXSpring = useSpring(x, SPRING_CONFIG);
  const mouseYSpring = useSpring(y, SPRING_CONFIG);

  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5], ["8deg", "-8deg"]);
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5], ["-8deg", "8deg"]);

  const glareX = useTransform(mouseXSpring, [-0.5, 0.5], ["0%", "100%"]);
  const glareY = useTransform(mouseYSpring, [-0.5, 0.5], ["0%", "100%"]);
  const glareBackground = useMotionTemplate`radial-gradient(circle at ${glareX} ${glareY}, rgba(255,255,255,0.3) 0%, rgba(255,255,255,0) 60%)`;
  const spotlightBackground = useMotionTemplate`radial-gradient(400px circle at ${mouseXPx}px ${mouseYPx}px, rgba(255,255,255,0.7), transparent 45%)`;

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!cardRef.current) return;
    const rect = cardRef.current.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;
    x.set(localX / rect.width - 0.5);
    y.set(localY / rect.height - 0.5);
    mouseXPx.set(localX);
    mouseYPx.set(localY);
  };

  const handleMouseEnter = () => setHovered(true);
  const handleMouseLeave = () => {
    x.set(0);
    y.set(0);
    setHovered(false);
  };

  return (
    <div style={{ perspective: 800, ...style }} className={`jr-tilt-card ${className ?? ''}`}>
      <motion.div
        ref={cardRef}
        onMouseMove={handleMouseMove}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        style={{
          rotateX,
          rotateY,
          transformStyle: "preserve-3d",
        }}
        animate={{
          scale: hovered ? 1.04 : 1,
          zIndex: hovered ? 20 : 1,
        }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        className="relative w-full h-full rounded-2xl overflow-hidden p-[1.5px] bg-white/5"
      >
        <AnimatePresence>
          {hovered && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.5 }}
              className="absolute inset-0 z-0 pointer-events-none"
              style={{ background: spotlightBackground }}
            />
          )}
        </AnimatePresence>

        <div className="relative w-full h-full rounded-[14px] overflow-hidden bg-zinc-950 z-10">
          {children}

          <AnimatePresence>
            {hovered && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
                className="absolute inset-0 pointer-events-none mix-blend-overlay z-20"
                style={{ background: glareBackground }}
              />
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </div>
  );
}
