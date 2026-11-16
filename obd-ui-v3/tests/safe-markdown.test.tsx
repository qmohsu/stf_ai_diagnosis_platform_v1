import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { SafeMarkdown } from "@/components/safe-markdown";

test("keeps Markdown structure and column alignment while filtering raw table attributes", () => {
  const content = [
    "# 接腳說明",
    "",
    "| 接頭 |",
    "| :---: |",
    "| **接地線** |",
    "",
    '<table><tr><td align="right" style="color:red" data-markdown-align="left">原文接腳</td></tr></table>',
  ].join("\n");
  const { container } = render(<SafeMarkdown content={content} manualId="11111111-1111-4111-8111-111111111111" />);

  expect(screen.getByRole("heading", { name: "接腳說明" })).toBeVisible();
  expect(screen.getByText("接地線").tagName).toBe("STRONG");
  expect(screen.getByRole("columnheader", { name: "接頭" })).toHaveAttribute("data-markdown-align", "center");
  expect(screen.getByRole("cell", { name: "接地線" })).toHaveAttribute("data-markdown-align", "center");
  const rawCell = screen.getByRole("cell", { name: "原文接腳" });
  expect(rawCell).not.toHaveAttribute("align");
  expect(rawCell).not.toHaveAttribute("data-markdown-align");
  expect(container.querySelector("[style]")).toBeNull();
});

test("keeps nested Markdown paragraphs in their HTML cell and drops nested content of blocked tags", () => {
  const content = [
    "<table><tr><td>",
    "",
    "**接地線**",
    "",
    "</td></tr></table>",
    "",
    "前文 <iframe>**隱藏文字**</iframe> 後文",
    "",
    "前文 <style>**隱藏樣式**</style> 後文",
    "",
    "前文 <script>**隱藏腳本**</script> 後文",
  ].join("\n");
  const { container } = render(<SafeMarkdown content={content} manualId="11111111-1111-4111-8111-111111111111" />);

  expect.soft(screen.getByRole("cell")).toContainElement(screen.getByText("接地線"));
  expect.soft(screen.getByText("接地線").tagName).toBe("STRONG");
  expect.soft(container).not.toHaveTextContent(/隱藏文字|隱藏樣式|隱藏腳本/);
});
