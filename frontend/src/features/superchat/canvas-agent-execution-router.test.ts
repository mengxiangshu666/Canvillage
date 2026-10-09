import { describe, expect, it } from "vitest";

import { routeCanvasAgentExecution } from "./canvas-agent-execution-router";

describe("routeCanvasAgentExecution", () => {
  it("keeps plan-first production requests read-only", () => {
    const route = routeCanvasAgentExecution(
      "想做一个宏观效应短片，大概30秒。先读取当前画布和素材，给出最短可执行路径；需要改结构时先提案，生成前单独说明。",
    );
    expect(route).toMatchObject({
      lane: "plan_only",
      mayWriteCanvas: false,
      mayStartWorkflow: false,
    });
    expect(routeCanvasAgentExecution("不要动画布，只告诉我问题在哪").lane).toBe("plan_only");
  });

  it("lets the model compile task facts before the server chooses a workflow", () => {
    expect(routeCanvasAgentExecution("帮我做水果短剧并直接出片")).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(routeCanvasAgentExecution("采用刚才的方案，开始执行").lane).toBe("canvas_execute");
    expect(routeCanvasAgentExecution("帮我做短片", { intentId: "one_click_film" })).toMatchObject({
      lane: "canvas_execute",
      reason: "model_decides_from_full_request",
      mayStartWorkflow: false,
    });
  });

  it("keeps explicit discussion-only wording read-only before semantic hints", () => {
    expect(
      routeCanvasAgentExecution("我们先聊一下这个画布该怎么做", {
        intentId: "workflow_build",
      }),
    ).toMatchObject({
      lane: "plan_only",
      mayWriteCanvas: false,
      mayStartWorkflow: false,
    });
    expect(
      routeCanvasAgentExecution("先说说怎么优化这个节点", {
        intentId: "prompt_polish",
      }),
    ).toMatchObject({
      lane: "plan_only",
      mayWriteCanvas: false,
      mayStartWorkflow: false,
    });
  });

  it("does not read a demand for action as a no-write boundary", () => {
    // 句首的「不要/别」修饰的是后面那一小截，不是句尾的动手指令。
    for (const text of [
      "不要只给我方案，直接动手",
      "别问了，直接动手",
      "给我方案，不要废话，直接动手",
      "不要问，直接动手",
    ]) {
      expect(routeCanvasAgentExecution(text)).toMatchObject({
        lane: "canvas_execute",
        mayWriteCanvas: true,
        mayStartWorkflow: false,
      });
    }
    // 确认过的方案是开工信号，不是「先看方案」。
    expect(routeCanvasAgentExecution("方案我确认了，开工")).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    // 取紧邻的否定仍然拦住。
    expect(routeCanvasAgentExecution("不要直接动手").lane).toBe("plan_only");
    expect(routeCanvasAgentExecution("先别急着改画布").lane).toBe("plan_only");
  });

  it("keeps prompt-polish work on the execution lane even when the user says不要生成", () => {
    expect(
      routeCanvasAgentExecution("优化角色正负提示词，让运镜更稳，不要生成测试", {
        intentId: "prompt_polish",
      }),
    ).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
  });

  it("reserves direct chat for explicit greetings", () => {
    expect(routeCanvasAgentExecution("嗨").lane).toBe("direct_chat");
    expect(routeCanvasAgentExecution("你好！").lane).toBe("direct_chat");
    expect(routeCanvasAgentExecution("你好，帮我看看当前画布").lane).toBe("canvas_execute");
  });

  it("gives ordinary canvas-context requests tool capability without forcing a tool call", () => {
    expect(routeCanvasAgentExecution("这个画布为什么这么慢")).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(routeCanvasAgentExecution("分析一下当前节点失败的原因").lane).toBe("canvas_execute");
    expect(routeCanvasAgentExecution("把这个问题处理好").lane).toBe("canvas_execute");
  });

  it("leaves explicit canvas mutations to the canvas executor lane", () => {
    expect(routeCanvasAgentExecution("把选中节点扩成三个镜头")).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(routeCanvasAgentExecution("把这个修一下")).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(routeCanvasAgentExecution("赶紧干活，优化一下这个画布")).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(
      routeCanvasAgentExecution(
        "请先读取当前画布避免覆盖已有节点，然后创建两个文字节点并连线；不要启动图片或视频生成。",
        { intentId: "workflow_build" },
      ),
    ).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(
      routeCanvasAgentExecution("确认，立即把刚才规划的节点和连线写入当前画布", {
        intentId: "workflow_build",
      }),
    ).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
  });
});
