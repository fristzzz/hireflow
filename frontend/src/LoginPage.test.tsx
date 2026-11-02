import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { expect, test, vi } from "vitest";

import App from "./App";
import LoginPage from "./LoginPage";

const wrap = (ui: ReactElement) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

test("未登录时渲染登录页", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ detail: "未登录" }), { status: 401 })),
  );
  wrap(<App />);
  expect(await screen.findByRole("button", { name: "登录" })).toBeInTheDocument();
  expect(screen.getByLabelText("用户名")).toBeInTheDocument();
  expect(screen.getByLabelText("密码")).toBeInTheDocument();
  expect(screen.queryByText("还没有岗位案卷")).not.toBeInTheDocument();
});

test("登录成功后进入主界面", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) {
        return new Response(JSON.stringify({ detail: "未登录" }), { status: 401 });
      }
      if (url.endsWith("/api/auth/login") && init?.method === "POST") {
        return new Response(JSON.stringify({ username: "test-admin" }));
      }
      return new Response(JSON.stringify([]));
    }),
  );
  wrap(<App />);
  fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "test-admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByText("还没有岗位案卷")).toBeInTheDocument();
});

test("登录失败展示错误提示", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "用户名或密码错误" }), { status: 401 }),
    ),
  );
  render(<LoginPage onLoggedIn={() => undefined} />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
});
