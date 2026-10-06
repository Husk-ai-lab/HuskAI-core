import { render, screen } from "@testing-library/react";
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
});
