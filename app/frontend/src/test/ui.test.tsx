import { fireEvent, render, screen } from "@testing-library/react";
import { AiKeyField } from "../components/AiKeyField";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Markdown } from "../components/Markdown";
import { Login } from "../pages/Login";
import { I18nProvider } from "../i18n";

describe("UI", () => {
  it("login has no guest access", () => {
    render(
      <I18nProvider initial="en">
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter><Login /></MemoryRouter>
        </QueryClientProvider>
      </I18nProvider>,
    );
    expect(screen.getByRole("heading", { name: "Sign in to Husk" })).toBeInTheDocument();
    expect(screen.queryByText(/guest|ospite/i)).not.toBeInTheDocument();
  });

  it("renders agent markdown safely", () => {
    render(<Markdown text={"# Titolo\n- **uno** [[note_1]]\n<script>x</script>"} />);
    expect(screen.getByRole("heading", { name: "Titolo" })).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });

  it("AI key field is masked until shown", () => {
    let value = "";
    render(<I18nProvider initial="en"><AiKeyField value="rg-secret-1234" onChange={(v) => { value = v; }} /></I18nProvider>);
    const input = screen.getByPlaceholderText(/./) as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: /show/i }));
    expect(input.type).toBe("text");
    fireEvent.change(input, { target: { value: "rg-new-key-5678" } });
    expect(value).toBe("rg-new-key-5678");
  });
});
