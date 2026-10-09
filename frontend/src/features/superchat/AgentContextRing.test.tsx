// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentContextRing } from "./AgentContextRing";
import { estimateAgentContextUsage } from "./agent-context-usage";

describe("AgentContextRing", () => {
  it("renders the remaining window as an accessible ring", () => {
    const usage = estimateAgentContextUsage({
      model: { id: "direct/model", label: "模型", contextLength: 10_000, maxOutputTokens: 1_000 },
      messages: [{ id: "1", role: "user", text: "搭建一个三镜头分镜", turnId: "t", timestamp: 1 }],
      draft: "把第二镜改成夜景",
    });

    render(<AgentContextRing usage={usage} />);

    const ring = screen.getByRole("progressbar");
    expect(ring).toHaveAttribute("aria-valuenow", String(usage.remainingPercent));
    expect(ring).toHaveAttribute("aria-valuetext", usage.label);
    expect(ring).toHaveAttribute("title", `${usage.label} · ${usage.detail}`);
    expect(document.querySelector(".village-agent-context-ring__value"))
      .toHaveAttribute("stroke-dashoffset", String(100 - usage.remainingPercent));
  });

  it("keeps the unknown-window state honest", () => {
    const usage = estimateAgentContextUsage({
      model: { id: "model", label: "模型" },
      messages: [],
    });

    render(<AgentContextRing usage={usage} />);

    expect(screen.getByRole("status")).toHaveAccessibleName(`${usage.label}。${usage.detail}`);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
