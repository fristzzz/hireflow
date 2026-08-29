import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import App, { EvaluationDetail, ResultsDesk, ResumeDesk } from "./App";
import type { Evaluation, Job } from "./types";

test("展示岗位案卷并允许创建第一个岗位", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/health")) {
        return new Response(JSON.stringify({ status: "ok", model_configured: true }));
      }
      return new Response(JSON.stringify([]));
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("还没有岗位案卷")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建第一个岗位" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "打开回收站" })).toBeInTheDocument();
});

test("候选人详情展示每个评估维度的独立分数", () => {
  const evaluation: Evaluation = {
    id: "evaluation-1",
    run_id: "run-1",
    resume_id: "resume-1",
    resume_name: "候选人.pdf",
    status: "completed",
    result: {
      candidate: { name: "候选人", current_role: "后端工程师" },
      overall_score: 86,
      match_level: "high",
      confidence: 0.9,
      summary: "工程经历与岗位高度匹配。",
      criteria: [
        {
          criterion_id: "D01",
          criterion: "Python 工程能力",
          weight: 25,
          score: 92,
          weighted_score: 23,
          status: "met",
          evidence: [{ text: "五年 Python 后端经验", location: "第 1 页" }],
        },
      ],
      strengths: ["经验扎实"],
      gaps: [],
      risk_flags: [],
      interview_questions: ["如何设计任务系统？"],
    },
    error: null,
    attempts: 1,
    review_status: "pending",
    review_note: "",
    screened_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
  };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <EvaluationDetail evaluation={evaluation} onClose={() => undefined} />
    </QueryClientProvider>,
  );

  expect(screen.getByRole("heading", { name: "维度评分" })).toBeInTheDocument();
  expect(screen.getByLabelText("Python 工程能力评分")).toHaveTextContent("92 / 100");
  expect(screen.getByLabelText("Python 工程能力评分")).toHaveTextContent("贡献 23 / 25");
  expect(screen.getByRole("separator", { name: "调整详情面板宽度" })).toBeInTheDocument();
  expect(screen.getByRole("separator", { name: "调整分析与简历宽度" })).toBeInTheDocument();
  expect(screen.getByTitle("简历预览")).toHaveAttribute("src", "/api/resumes/resume-1/preview");
});

test("简历材料可按格式分类并按名称排序", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok", model_configured: true, vision_enabled: false }));
    }
    return new Response(JSON.stringify([
      { id: "resume-b", job_id: "job-1", original_name: "周明.pdf", mime_type: "application/pdf", size_bytes: 2000, page_count: 3, parse_status: "uploaded", parse_error: null, created_at: "2026-08-10T02:00:00Z" },
      { id: "resume-a", job_id: "job-1", original_name: "安然.docx", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size_bytes: 1000, page_count: 1, parse_status: "ready", parse_error: null, created_at: "2026-08-10T01:00:00Z" },
    ]));
  }));
  const job: Job = { id: "job-1", name: "后端工程师", jd_text: "JD", prompt: "标准", resume_count: 2, run_count: 0, created_at: "2026-08-10T00:00:00Z", updated_at: "2026-08-10T00:00:00Z" };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResumeDesk job={job} onRunCreated={() => undefined} /></QueryClientProvider>);
  expect(await screen.findByText("周明.pdf")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("简历格式"), { target: { value: "docx" } });
  expect(screen.queryByText("周明.pdf")).not.toBeInTheDocument();
  expect(screen.getByText("安然.docx")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看安然.docx文本" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "安然.docx更多操作" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("简历格式"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("简历排序"), { target: { value: "name-asc" } });
  expect(screen.getAllByTestId("resume-name").map((item) => item.textContent)).toEqual(["安然.docx", "周明.pdf"]);
});

test("筛选结果可按人工状态分类并按姓名排序", async () => {
  const result = (name: string, score: number, withBackground = false): Evaluation["result"] => ({
    candidate: { name, current_role: "工程师" }, overall_score: score, match_level: score >= 80 ? "high" : "medium", confidence: 0.9,
    summary: "候选人摘要", criteria: [], strengths: [], gaps: [], risk_flags: [], interview_questions: [],
    background_highlights: withBackground ? [
      { kind: "company", name: "字节跳动", bonus_points: 3, evidence: [{ text: "任职字节跳动", location: "第 1 页" }] },
      { kind: "school", name: "浙江大学", bonus_points: 2, evidence: [{ text: "毕业于浙江大学", location: "第 1 页" }] },
    ] : [],
  });
  const evaluations: Evaluation[] = [
    { id: "evaluation-b", run_id: "run-2", resume_id: "resume-b", resume_name: "周明.pdf", status: "completed", result: result("周明", 90, true), error: null, attempts: 1, review_status: "advance", review_note: "", screened_at: "2026-08-10T02:00:00Z", updated_at: "2026-08-10T02:00:00Z" },
    { id: "evaluation-a", run_id: "run-1", resume_id: "resume-a", resume_name: "安然.pdf", status: "completed", result: result("安然", 70), error: null, attempts: 1, review_status: "pending", review_note: "", screened_at: "2026-08-09T01:00:00Z", updated_at: "2026-08-10T01:00:00Z" },
  ];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/jobs/job-1/evaluations")) {
      const params = new URL(url, "http://localhost").searchParams;
      let items = evaluations.filter((item) => params.get("review_status") === "all" || item.review_status === params.get("review_status"));
      if (params.get("sort") === "name-asc") items = [...items].sort((a, b) => (a.result?.candidate.name || "").localeCompare(b.result?.candidate.name || "", "zh-CN"));
      return new Response(JSON.stringify({ items, page: 1, page_size: 10, total: params.get("review_status") === "all" ? 12 : items.length, total_pages: params.get("review_status") === "all" ? 2 : 1 }));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResultsDesk jobId="job-1" initialRunId="run-1" /></QueryClientProvider>);
  expect(await screen.findByText("周明")).toBeInTheDocument();
  expect(screen.getByText("名企 · 字节跳动")).toBeInTheDocument();
  expect(screen.getByText("名校 · 浙江大学")).toBeInTheDocument();
  expect(screen.queryByLabelText("筛选批次")).not.toBeInTheDocument();
  expect(screen.getByText("2026年8月10日")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "导出 Excel" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=xlsx");
  expect(screen.getByRole("link", { name: "导出 CSV" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=csv");
  expect(screen.getByRole("link", { name: "导出 JSON" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=json");
  expect(screen.getByRole("link", { name: "导出 Markdown" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=md");
  expect(screen.getByRole("link", { name: "导出 Word" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=docx");
  expect(screen.getByText("第 1 / 2 页")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("checkbox", { name: "选择周明" }));
  expect(screen.getByText("已选 1 人")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批量推进" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批量重新评分" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批量移入回收站" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "导出 Excel" })).toHaveAttribute(
    "href",
    "/api/jobs/job-1/export?format=xlsx&evaluation_id=evaluation-b",
  );
  expect(screen.getByRole("link", { name: "导出 Word" })).toHaveAttribute(
    "href",
    "/api/jobs/job-1/export?format=docx&evaluation_id=evaluation-b",
  );

  fireEvent.change(screen.getByLabelText("人工状态"), { target: { value: "advance" } });
  await waitFor(() => expect(screen.queryByText("安然")).not.toBeInTheDocument());
  expect(await screen.findByText("周明")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("人工状态"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("结果排序"), { target: { value: "name-asc" } });
  await waitFor(() => expect(screen.getAllByTestId("candidate-name").map((item) => item.textContent)).toEqual(["安然", "周明"]));
});

test("未识别姓名回退到简历文件名，评估失败不显示为解析失败", async () => {
  const evaluations: Evaluation[] = [
    {
      id: "evaluation-unknown",
      run_id: "run-1",
      resume_id: "resume-1",
      resume_name: "江贺琨_15年.pdf",
      status: "completed",
      result: {
        candidate: { name: "未识别", current_role: "测试工程师" },
        overall_score: 60,
        match_level: "low",
        confidence: 0.4,
        summary: "证据不足",
        criteria: [],
        strengths: [],
        gaps: [],
        risk_flags: [],
        interview_questions: [],
      },
      error: null,
      attempts: 1,
      review_status: "pending",
      review_note: "",
      screened_at: "2026-08-28T05:11:08Z",
      updated_at: "2026-08-28T05:11:08Z",
    },
    {
      id: "evaluation-failed",
      run_id: "run-2",
      resume_id: "resume-2",
      resume_name: "侯东鹏_11年.pdf",
      status: "failed",
      result: null,
      error: "评估模型未返回结果，不是简历文件解析失败，请重试",
      attempts: 2,
      review_status: "pending",
      review_note: "",
      screened_at: "2026-08-28T06:30:42Z",
      updated_at: "2026-08-28T06:30:42Z",
    },
  ];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/jobs/job-1/evaluations")) {
      return new Response(JSON.stringify({ items: evaluations, page: 1, page_size: 10, total: 2, total_pages: 1 }));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResultsDesk jobId="job-1" /></QueryClientProvider>);
  expect(await screen.findByText("江贺琨_15年.pdf")).toBeInTheDocument();
  expect(screen.getByText("侯东鹏_11年.pdf")).toBeInTheDocument();
  expect(screen.getByText("评估模型未返回结果，不是简历文件解析失败，请重试")).toBeInTheDocument();
  expect(screen.queryByText("解析失败")).not.toBeInTheDocument();
});
