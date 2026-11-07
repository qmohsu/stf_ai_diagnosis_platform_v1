import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { redirect } from "next/navigation";
import LoginPage from "@/app/login/page";
import { LoginForm } from "@/components/login-form";
import { currentPortalSession, currentPortalUserSafely } from "@/server/auth-session";

vi.mock("@/server/auth-session", () => ({
  currentPortalSession: vi.fn(),
  currentPortalUserSafely: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  redirect: vi.fn(() => { throw new Error("NEXT_REDIRECT"); }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("login experience", () => {
  it("issue #270: keeps Next.js redirect control flow for an already signed-in user", async () => {
    vi.mocked(currentPortalSession).mockResolvedValue({
      token: "test-token",
      user: { id: "user-id", username: "test.tech", email: null, display_name: null, memberships: [] },
    });

    await expect(LoginPage()).rejects.toThrow("NEXT_REDIRECT");
    expect(redirect).toHaveBeenCalledWith("/");
  });

  it("issue #270: shows a retry state instead of the login form when an existing session cannot be checked", async () => {
    vi.mocked(currentPortalSession).mockRejectedValue(new Error("V3 unavailable"));
    vi.mocked(currentPortalUserSafely).mockResolvedValue(null);

    render(await LoginPage());

    expect(screen.getByRole("alert")).toHaveTextContent("服務暫時無法使用");
    expect(screen.getByRole("link", { name: "重新整理" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("button", { name: "登入" })).not.toBeInTheDocument();
  });

  it("issue #270: trims only the username before sending credentials", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ error: { code: "AUTH_INVALID_CREDENTIALS" } }, { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    render(<LoginForm />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("用戶名稱"), "  test.tech  ");
    await user.type(screen.getByLabelText("密碼"), " password with spaces ");
    await user.click(screen.getByRole("button", { name: "登入" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      username: "test.tech",
      password: " password with spaces ",
    });
  });

  it("issue #270: explains a rejected login Origin separately from incorrect credentials", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ error: { code: "ORIGIN_REJECTED" } }, { status: 403 })));
    render(<LoginForm />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("用戶名稱"), "test.tech");
    await user.type(screen.getByLabelText("密碼"), "valid-password");
    await user.click(screen.getByRole("button", { name: "登入" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("網址");
  });

  it.each([
    [401, "用戶名稱或密碼不正確"],
    [429, "嘗試次數過多"],
    [503, "服務暫時無法使用"],
  ])("issue #270: preserves the distinct %i login failure message", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ error: { code: "TEST" } }, { status })));
    render(<LoginForm />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("用戶名稱"), "test.tech");
    await user.type(screen.getByLabelText("密碼"), "valid-password");
    await user.click(screen.getByRole("button", { name: "登入" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText("密碼")).toHaveValue("");
  });
});
